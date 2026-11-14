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
from .snapshots import NoActiveBuild, list_articles, pinned_build, resolve, timeline
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
        return {"build_id": build_id, "articles": list_articles(conn, build_id, q)}


@app.get("/snapshot")
def snapshot(wiki_id: str, page_id: int, at: str, sections: bool = True):
    t, date_only = _t(at, "at")
    with _db() as conn:
        build_id = pinned_build(conn)
        snap = resolve(conn, build_id, wiki_id, page_id, t, settings().snapshot_mode, with_sections=sections)
    return {"build_id": build_id, "date_only_end_of_day": date_only, "snapshot": snap}


@app.get("/timeline")
def get_timeline(wiki_id: str, page_id: int, start: str, end: str):
    s, _ = _t(start, "start")
    e, _ = _t(end, "end")
    with _db() as conn:
        build_id = pinned_build(conn)
        return {"build_id": build_id, "states": timeline(conn, build_id, wiki_id, page_id, s, e)}


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
