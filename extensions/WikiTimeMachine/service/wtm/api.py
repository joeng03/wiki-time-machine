"""Internal HTTP API consumed by the MediaWiki extension's server-side client (read-only credentials)."""
from __future__ import annotations

import time
from typing import Callable

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .answer import OpenAICompatClient, synthesize
from .config import settings
from .db import server_capabilities, session
from .diff import diff_sections
from .embed import get_embedder
from .retrieval import corpus_search, revision_evidence
from .snapshots import NoActiveBuild, list_articles, pinned_build, resolve, timeline, coverage, page_info, oldid_url
from .timeutil import TimeInputError, parse_utc

app = FastAPI(title="WikiTimeMachine service", version=__version__)

# Overridable in tests.
STATE: dict = {"llm": None, "embedder": None, "db_kwargs": {}}


def _embedder():
    if STATE["embedder"] is None:
        STATE["embedder"] = get_embedder(settings().embedder)
    return STATE["embedder"]


def _llm() -> Callable | None:
    if STATE["llm"] is not None:
        return STATE["llm"] if callable(STATE["llm"]) else None
    cfg = settings()
    if cfg.llm_enabled:
        import json as _json
        extra = _json.loads(cfg.llm_extra_body) if cfg.llm_extra_body else {}
        return OpenAICompatClient(cfg.llm_base_url, cfg.llm_model, cfg.llm_api_key, cfg.llm_timeout,
                                  cfg.llm_max_tokens, extra, cfg.llm_structured)
    return None


def _db():
    return session("reader", **STATE["db_kwargs"])


def _t(value: str, name: str):
    try:
        return parse_utc(value)
    except TimeInputError as exc:
        raise HTTPException(400, detail={"code": "invalid_time", "field": name, "message": str(exc)})


@app.exception_handler(NoActiveBuild)
def _no_build(_req, exc):
    return JSONResponse(status_code=503, content={"detail": {"code": "no_build", "message": str(exc)}})


@app.get("/health")
def health():
    with _db() as conn:
        caps = server_capabilities(conn)
        try:
            build_id = pinned_build(conn)
        except NoActiveBuild:
            build_id = None
    cfg = settings()
    return {"ok": caps["version_ok"] and caps["vector_supported"], "db": caps, "active_build": build_id,
            "snapshot_mode": cfg.snapshot_mode, "embedder": cfg.embedder, "llm_enabled": cfg.llm_enabled}


@app.get("/articles")
def articles(q: str = Query("", max_length=200)):
    with _db() as conn:
        build_id = pinned_build(conn)
        arts = list_articles(conn, build_id, q)
        from .claims import active_claim_build
        cbid = active_claim_build(conn, build_id)
        with conn.cursor() as cur:
            for a in arts:
                n = 0
                if cbid:
                    cur.execute("SELECT COUNT(*) n FROM wiki_claim_snapshot FOR SYSTEM_TIME ALL WHERE claim_build_id=%s "
                                "AND wiki_id=%s AND page_id=%s", (cbid, a["wiki_id"], a["page_id"]))
                    n = cur.fetchone()["n"]
                a["claim_rows"] = n
        return {"build_id": build_id, "articles": arts}


@app.get("/snapshot")
def snapshot(wiki_id: str, page_id: int, at: str, sections: bool = True):
    t, date_only = _t(at, "at")
    with _db() as conn:
        build_id = pinned_build(conn)
        snap = resolve(conn, build_id, wiki_id, page_id, t, settings().snapshot_mode, with_sections=sections)
    return {"build_id": build_id, "date_only_end_of_day": date_only, "snapshot": snap}


@app.get("/timeline")
def get_timeline(wiki_id: str, page_id: int, start: str, end: str, limit: int = Query(1000, ge=1, le=1000)):
    s, _ = _t(start, "start")
    e, _ = _t(end, "end")
    if s >= e:
        raise HTTPException(400, detail={"code": "invalid_range", "message": "The timeline start must be before its end."})
    from .claims import active_claim_build, attach_timeline_claims
    from .timeutil import iso
    with _db() as conn:
        build_id = pinned_build(conn)
        page = page_info(conn, wiki_id, page_id)
        if page is None:
            raise HTTPException(404, detail={"code": "unknown_page", "message": "Article not ingested."})
        states = timeline(conn, build_id, wiki_id, page_id, s, e, limit + 1)
        truncated = len(states) > limit
        states = states[:limit]
        for state in states:
            state["url"] = oldid_url(page["source_host"], state["revision_id"])
            state["claims"] = []
        cbid = active_claim_build(conn, build_id)
        if cbid:
            attach_timeline_claims(conn, build_id, cbid, wiki_id, page_id, states)
        windows = [{"start": iso(max(s, w["coverage_start"])), "end": iso(min(e, w["coverage_end"]))}
                   for w in coverage(conn, build_id, wiki_id, page_id)
                   if w["coverage_start"] < e and w["coverage_end"] > s]
        return {"build_id": build_id, "claim_build_id": cbid, "states": states, "windows": windows,
                "truncated": truncated, "limit": limit}


def _resolve_pair(conn, wiki_id: str, page_id: int, before: str, after: str, sections: bool):
    tb, db_ = _t(before, "before")
    ta, da = _t(after, "after")
    if tb > ta:
        raise HTTPException(400, detail={"code": "reversed_dates", "message": "The first date must not be after the second date."})
    build_id = pinned_build(conn)
    mode = settings().snapshot_mode
    sb = resolve(conn, build_id, wiki_id, page_id, tb, mode, with_sections=sections)
    sa = resolve(conn, build_id, wiki_id, page_id, ta, mode, with_sections=sections)
    if sb.get("status") == "unknown_page":
        raise HTTPException(404, detail={"code": "unknown_page", "message": "Article not ingested."})
    return build_id, sb, sa, {"before": db_, "after": da}


@app.get("/compare")
def compare(wiki_id: str, page_id: int, before: str, after: str):
    t0 = time.perf_counter()
    with _db() as conn:
        build_id, sb, sa, date_only = _resolve_pair(conn, wiki_id, page_id, before, after, sections=True)
    diff = None
    if sb["status"] == "ok" and sa["status"] == "ok":
        diff = diff_sections(sb["sections"], sa["sections"])
        diff["same_revision"] = sb["revision_id"] == sa["revision_id"]
    return {"build_id": build_id, "date_only_end_of_day": date_only, "before": sb, "after": sa, "diff": diff,
            "timing_ms": round((time.perf_counter() - t0) * 1000, 1)}


class AskBody(BaseModel):
    wiki_id: str = Field(max_length=32)
    page_id: int
    before: str = Field(max_length=40)
    after: str = Field(max_length=40)
    question: str = Field(min_length=3, max_length=500)
    k: int = Field(default=5, ge=1, le=10)


@app.post("/ask")
def ask(body: AskBody):
    timings = {}
    t0 = time.perf_counter()
    emb = _embedder()
    with _db() as conn:
        build_id, sb, sa, date_only = _resolve_pair(conn, body.wiki_id, body.page_id, body.before, body.after, sections=False)
        timings["resolve_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        t1 = time.perf_counter()
        qvec = emb.embed([body.question])[0]
        timings["embed_ms"] = round((time.perf_counter() - t1) * 1000, 1)
        t2 = time.perf_counter()
        ev = {}
        for key, side, prefix in (("before", sb, "A"), ("after", sa, "B")):
            ev[key] = (revision_evidence(conn, body.wiki_id, side["revision_id"], qvec, emb.model_id, body.k, prefix, side["url"])
                       if side["status"] == "ok" else [])
        timings["retrieval_ms"] = round((time.perf_counter() - t2) * 1000, 1)
    t3 = time.perf_counter()
    gen = synthesize(body.question, sb, sa, ev["before"], ev["after"], _llm())
    timings["generation_ms"] = round((time.perf_counter() - t3) * 1000, 1)
    sides = {}
    for key, side in (("before", sb), ("after", sa)):
        if side["status"] != "ok":
            sides[key] = {"status": side["status"], "message": side.get("message")}
        elif not ev[key]:
            sides[key] = {"status": "insufficient_evidence", "message": "Insufficient evidence in this revision."}
        else:
            sides[key] = {"status": "evidence"}
    return {"build_id": build_id, "question": body.question, "date_only_end_of_day": date_only,
            "before": sb, "after": sa, "evidence": ev, "sides": sides, "generation": gen, "timing_ms": timings}


@app.get("/claims")
def claims(wiki_id: str, page_id: int, before: str, after: str, valid_at: str = Query(..., max_length=10)):
    """Bitemporal: as of each side's Wikipedia time, who did the article say held the office on `valid_at`?"""
    from datetime import date as _date

    from .claims import BITEMPORAL_SQL, active_claim_build, claim_history, claims_as_of
    try:
        d = _date.fromisoformat(valid_at)
    except ValueError:
        raise HTTPException(400, detail={"code": "invalid_date", "message": "valid_at must be YYYY-MM-DD"})
    with _db() as conn:
        build_id, sb, sa, date_only = _resolve_pair(conn, wiki_id, page_id, before, after, sections=False)
        cbid = active_claim_build(conn, build_id)
        out = {"build_id": build_id, "claim_build_id": cbid, "valid_at": d.isoformat(), "sql": BITEMPORAL_SQL, "sides": {}}
        if cbid is None:
            return {**out, "status": "no_claims"}
        for key, side in (("before", sb), ("after", sa)):
            entry = {"snapshot": {k: side.get(k) for k in ("status", "requested_at", "revision_id", "source_timestamp", "url", "message")}}
            if side["status"] in ("ok", "unavailable", "no_revision"):
                t, _ = parse_utc(before if key == "before" else after)
                entry["claims"] = claims_as_of(conn, build_id, cbid, wiki_id, page_id, t, d,
                                               side.get("revision_id") if side["status"] == "ok" else None, side.get("url"))
            else:
                entry["claims"] = None
            out["sides"][key] = entry
        out["history"] = claim_history(conn, cbid, wiki_id, page_id, d)
    return {**out, "status": "ok"}


@app.get("/search")
def search(q: str = Query(..., min_length=2, max_length=300), k: int = Query(10, ge=1, le=50)):
    emb = _embedder()
    t0 = time.perf_counter()
    qvec = emb.embed([q])[0]
    with _db() as conn:
        t1 = time.perf_counter()
        hits = corpus_search(conn, qvec, k)
        t2 = time.perf_counter()
    return {"query": q, "results": hits, "timing_ms": {"embed": round((t1 - t0) * 1000, 1), "ann": round((t2 - t1) * 1000, 1)}}
