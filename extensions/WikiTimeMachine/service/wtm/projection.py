"""Build, validate and atomically publish the system-versioned historical projection.

The system period of wiki_snapshot is backfilled with the *upstream Wikipedia edit timeline*
(system_versioning_insert_history = ON in a dedicated importer session). No open-ended current row
is ever written: every interval is closed at its verified coverage end.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from .embed import Embedder, to_sql_vector
from .normalize import PARSER_VERSION, chunk_sections, embedding_input, extract_sections

US = timedelta(microseconds=1)


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class Rev:
    revision_id: int
    ts: datetime
    available: bool
    sha: str | None = None


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime
    revision_id: int
    available: bool
    sha: str | None = None


def merge_windows(windows: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Merge overlapping or touching half-open windows."""
    merged: list[list[datetime]] = []
    for s, e in sorted(windows):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def state_at(revs: list[Rev], t: datetime) -> Rev | None:
    """Latest revision with ts <= t; same-second ties -> greatest revision id. `revs` sorted by (ts, id)."""
    best = None
    for r in revs:
        if r.ts <= t:
            best = r
        else:
            break
    return best


def compute_intervals(revs: list[Rev], cov_start: datetime, cov_end: datetime) -> list[Interval]:
    """Non-overlapping half-open intervals inside [cov_start, cov_end).

    `revs` = archive revisions of the page (any order). Interval boundaries are the coverage start and
    every distinct revision timestamp inside the window; the last interval ends at cov_end. Times before
    the page's first revision are left as holes (no row).
    """
    revs = sorted(revs, key=lambda r: (r.ts, r.revision_id))
    points = sorted({cov_start} | {r.ts for r in revs if cov_start < r.ts < cov_end})
    out: list[Interval] = []
    for i, p in enumerate(points):
        end = points[i + 1] if i + 1 < len(points) else cov_end
        st = state_at(revs, p)
        if st is None:
            continue
        if out and out[-1].revision_id == st.revision_id and out[-1].end == p:
            out[-1] = Interval(out[-1].start, end, st.revision_id, st.available, st.sha)
        else:
            out.append(Interval(p, end, st.revision_id, st.available, st.sha))
    return out


# ---------------------------------------------------------------- staging reads

def complete_coverage(conn) -> dict[tuple[str, int], list[dict]]:
    with conn.cursor() as cur:
        cur.execute("SELECT wiki_id, page_id, coverage_start, coverage_end, predecessor_ids FROM wiki_fetch_window "
                    "WHERE status='complete' ORDER BY wiki_id, page_id, coverage_start")
        rows = cur.fetchall()
    out: dict[tuple[str, int], list[dict]] = {}
    for r in rows:
        out.setdefault((r["wiki_id"], r["page_id"]), []).append(r)
    return out


def page_revisions(conn, wiki_id: str, page_id: int, before: datetime) -> list[Rev]:
    with conn.cursor() as cur:
        cur.execute("SELECT revision_id, revision_ts, content_available, text_sha256 FROM wiki_revision "
                    "WHERE wiki_id=%s AND page_id=%s AND revision_ts < %s ORDER BY revision_ts, revision_id",
                    (wiki_id, page_id, before))
        return [Rev(r["revision_id"], r["revision_ts"], bool(r["content_available"]), r["text_sha256"])
                for r in cur.fetchall()]


def plan_build(conn) -> dict:
    """Compute merged coverage and intervals for every page with complete windows."""
    plan = {}
    for (wiki_id, page_id), wins in complete_coverage(conn).items():
        merged = merge_windows([(w["coverage_start"], w["coverage_end"]) for w in wins])
        revs = page_revisions(conn, wiki_id, page_id, max(e for _, e in merged))
        page_plan = []
        for s, e in merged:
            # Predecessor sanity: archive state just before s must match what the window fetch recorded.
            starters = [w for w in wins if w["coverage_start"] == s]
            recorded = json.loads(starters[0]["predecessor_ids"]) if starters and starters[0]["predecessor_ids"] else []
            pre = state_at(revs, s - US)
            if recorded and (pre is None or pre.revision_id not in recorded):
                raise BuildError(f"{wiki_id}:{page_id} predecessor mismatch at {s}: archive={pre}, recorded={recorded}")
            if not recorded and pre is not None and starters and starters[0]["predecessor_ids"] is not None:
                raise BuildError(f"{wiki_id}:{page_id} window at {s} recorded no predecessor but archive has {pre.revision_id}")
            page_plan.append({"start": s, "end": e, "intervals": compute_intervals(revs, s, e)})
        plan[(wiki_id, page_id)] = page_plan
    return plan


def fingerprint(plan: dict, parser_version: str, embed_model: str) -> str:
    canon = {"parser": parser_version, "model": embed_model, "pages": [
        [w, p, [[c["start"].isoformat(), c["end"].isoformat(),
                 [[i.start.isoformat(), i.end.isoformat(), i.revision_id, i.available, i.sha] for i in c["intervals"]]]
                for c in plan[(w, p)]]]
        for (w, p) in sorted(plan)]}
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------- derived text + chunks + vectors

def ensure_text_and_chunks(conn, wiki_id: str, revision_ids: list[int], embedder: Embedder,
                           parser_version: str = PARSER_VERSION, log: Callable[[str], None] = print) -> dict:
    stats = {"normalized": 0, "chunks_embedded": 0, "chunks_reused": 0, "revisions_chunked": 0}
    with conn.cursor() as cur:
        pending: dict[int, list] = {}
        for rid in revision_ids:
            cur.execute("SELECT 1 FROM wiki_revision_chunk WHERE wiki_id=%s AND revision_id=%s AND parser_version=%s "
                        "AND embed_model=%s LIMIT 1", (wiki_id, rid, parser_version, embedder.model_id))
            have_chunks = cur.fetchone() is not None
            cur.execute("SELECT sections FROM wiki_revision_text WHERE wiki_id=%s AND revision_id=%s AND parser_version=%s",
                        (wiki_id, rid, parser_version))
            row = cur.fetchone()
            if row:
                sections = json.loads(row["sections"])
            else:
                cur.execute("SELECT wikitext FROM wiki_revision WHERE wiki_id=%s AND revision_id=%s AND content_available",
                            (wiki_id, rid))
                src = cur.fetchone()
                if not src:
                    continue
                sections = extract_sections(src["wikitext"])
                cur.execute("INSERT INTO wiki_revision_text (wiki_id, revision_id, parser_version, sections) VALUES (%s,%s,%s,%s)",
                            (wiki_id, rid, parser_version, json.dumps(sections, ensure_ascii=False)))
                stats["normalized"] += 1
            if not have_chunks:
                pending[rid] = chunk_sections(sections)
        # Embed each unique normalized chunk exactly once (per parser/model).
        unique: dict[str, str] = {}
        embed_in: dict[str, str] = {}
        for chunks in pending.values():
            for c in chunks:
                unique.setdefault(c.text_hash, c.text)
                embed_in.setdefault(c.text_hash, embedding_input(c.heading, c.text))
        hash_to_id: dict[str, int] = {}
        hashes = list(unique)
        for i in range(0, len(hashes), 500):
            part = hashes[i:i + 500]
            cur.execute("SELECT text_hash, chunk_id FROM wiki_chunk WHERE parser_version=%s AND embed_model=%s AND text_hash IN ("
                        + ",".join(["%s"] * len(part)) + ")", (parser_version, embedder.model_id, *part))
            for r in cur.fetchall():
                hash_to_id[r["text_hash"]] = r["chunk_id"]
        missing = [h for h in hashes if h not in hash_to_id]
        stats["chunks_reused"] = len(hashes) - len(missing)
        for i in range(0, len(missing), 64):
            part = missing[i:i + 64]
            vecs = embedder.embed([embed_in[h] for h in part])
            for h, v in zip(part, vecs):
                text = unique[h]
                cur.execute("INSERT INTO wiki_chunk (text_hash, parser_version, embed_model, text, token_estimate, embedding) "
                            "VALUES (%s,%s,%s,%s,%s,VEC_FromText(%s))",
                            (h, parser_version, embedder.model_id, text, int(len(text.split()) * 1.33), to_sql_vector(v)))
                hash_to_id[h] = cur.lastrowid
            stats["chunks_embedded"] += len(part)
            if len(missing) > 64:
                log(f"  embedded {min(i + 64, len(missing))}/{len(missing)} new chunks")
        for rid, chunks in pending.items():
            cur.execute("DELETE FROM wiki_revision_chunk WHERE wiki_id=%s AND revision_id=%s", (wiki_id, rid))
            if chunks:
                cur.executemany(
                    "INSERT INTO wiki_revision_chunk (wiki_id, revision_id, ordinal, chunk_id, parser_version, embed_model, "
                    "section_heading) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    [(wiki_id, rid, c.ordinal, hash_to_id[c.text_hash], parser_version, embedder.model_id, c.heading[:512])
                     for c in chunks])
            stats["revisions_chunked"] += 1
    return stats


# ---------------------------------------------------------------- build / validate / publish

def active_build(conn) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT b.* FROM wiki_active_build a JOIN wiki_build b ON b.build_id=a.build_id WHERE a.id=1")
        return cur.fetchone()


def build(conn, embedder: Embedder, parser_version: str = PARSER_VERSION, log: Callable[[str], None] = print,
          sample_seed: int = 7) -> dict:
    """`conn` must be an importer session with system_versioning_insert_history = ON."""
    plan = plan_build(conn)
    if not plan:
        raise BuildError("no complete coverage windows staged")
    fp = fingerprint(plan, parser_version, embedder.model_id)
    act = active_build(conn)
    if act and act["input_fingerprint"] == fp:
        log(f"inputs unchanged; reusing published build {act['build_id']}")
        return {"build_id": act["build_id"], "reused": True}
    with conn.cursor() as cur:
        cur.execute("SELECT build_id FROM wiki_build WHERE input_fingerprint=%s AND status IN ('validated','superseded') "
                    "ORDER BY build_id DESC LIMIT 1", (fp,))
        prior = cur.fetchone()
    if prior:
        log(f"identical earlier build {prior['build_id']} found; republishing it")
        publish(conn, prior["build_id"])
        return {"build_id": prior["build_id"], "reused": True}

    text_stats = {}
    for (wiki_id, page_id), cov in plan.items():
        rids = sorted({i.revision_id for c in cov for i in c["intervals"] if i.available})
        log(f"page {wiki_id}:{page_id}: {sum(len(c['intervals']) for c in cov)} intervals, {len(rids)} snapshot revisions")
        text_stats[f"{wiki_id}:{page_id}"] = ensure_text_and_chunks(conn, wiki_id, rids, embedder, parser_version, log)

    with conn.cursor() as cur:
        cur.execute("SELECT @@system_versioning_insert_history AS ih, @@time_zone AS tz")
        sess = cur.fetchone()
        if not sess["ih"] or sess["tz"] != "+00:00":
            raise BuildError("importer session must have system_versioning_insert_history=ON and time_zone=+00:00")
        cur.execute("INSERT INTO wiki_build (status, input_fingerprint, parser_version, embed_model) VALUES ('building',%s,%s,%s)",
                    (fp, parser_version, embedder.model_id))
        build_id = cur.lastrowid
        rows = 0
        for (wiki_id, page_id), cov in plan.items():
            for c in cov:
                cur.execute("INSERT INTO wiki_coverage (build_id, wiki_id, page_id, coverage_start, coverage_end) VALUES (%s,%s,%s,%s,%s)",
                            (build_id, wiki_id, page_id, c["start"], c["end"]))
                params = [(build_id, wiki_id, page_id, i.revision_id, i.available, i.start, i.end) for i in c["intervals"]]
                if params:
                    cur.executemany("INSERT INTO wiki_snapshot (build_id, wiki_id, page_id, revision_id, content_available, "
                                    "row_start, row_end) VALUES (%s,%s,%s,%s,%s,%s,%s)", params)
                    rows += len(params)
        cur.execute("UPDATE wiki_build SET snapshot_rows=%s WHERE build_id=%s", (rows, build_id))
    report = validate(conn, build_id, plan, seed=sample_seed)
    with conn.cursor() as cur:
        cur.execute("UPDATE wiki_build SET status=%s, validation=%s WHERE build_id=%s",
                    ("validated" if report["ok"] else "failed", json.dumps({**report, "text": text_stats}, default=str), build_id))
    if not report["ok"]:
        raise BuildError(f"build {build_id} failed validation: {report['failures'][:5]}")
    publish(conn, build_id)
    log(f"published build {build_id}: {rows} snapshot rows, {report['checks']} checks passed")
    return {"build_id": build_id, "reused": False, "snapshot_rows": rows, "validation": report, "text": text_stats}


def _as_of(cur, build_id: int, wiki_id: str, page_id: int, t: datetime):
    cur.execute("SELECT revision_id, content_available FROM wiki_snapshot FOR SYSTEM_TIME AS OF CAST(%s AS DATETIME(6)) "
                "WHERE build_id=%s AND wiki_id=%s AND page_id=%s", (t, build_id, wiki_id, page_id))
    rows = cur.fetchall()
    if len(rows) > 1:
        raise BuildError(f"overlap: {len(rows)} rows AS OF {t}")
    return rows[0]["revision_id"] if rows else None


def _archive(cur, wiki_id: str, page_id: int, t: datetime):
    cur.execute("SELECT revision_id FROM wiki_revision WHERE wiki_id=%s AND page_id=%s AND revision_ts <= %s "
                "ORDER BY revision_ts DESC, revision_id DESC LIMIT 1", (wiki_id, page_id, t))
    r = cur.fetchone()
    return r["revision_id"] if r else None


def validate(conn, build_id: int, plan: dict, seed: int = 7, samples_per_window: int = 25) -> dict:
    failures: list[str] = []
    checks = 0
    rng = random.Random(seed)

    def expect(label, got, want):
        nonlocal checks
        checks += 1
        if got != want:
            failures.append(f"{label}: got {got}, expected {want}")

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) n FROM wiki_snapshot WHERE build_id=%s", (build_id,))
        expect("no open-ended current rows", cur.fetchone()["n"], 0)
        cur.execute("SELECT DATETIME_PRECISION p FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME='wiki_snapshot' AND COLUMN_NAME='row_start'")
        expect("row_start precision", cur.fetchone()["p"], 6)
        utc_checked = False
        for (wiki_id, page_id), cov in plan.items():
            cov_starts = {c["start"] for c in cov}
            cov_ends = {c["end"] for c in cov}
            cur.execute("SELECT revision_id, row_start, row_end FROM wiki_snapshot FOR SYSTEM_TIME ALL "
                        "WHERE build_id=%s AND wiki_id=%s AND page_id=%s ORDER BY row_start", (build_id, wiki_id, page_id))
            stored = cur.fetchall()
            for a, b in zip(stored, stored[1:]):
                checks += 1
                if b["row_start"] < a["row_end"]:
                    failures.append(f"overlap {a} / {b}")
            expect(f"{page_id} row count", len(stored), sum(len(c["intervals"]) for c in cov))
            if stored and not utc_checked:
                first = stored[0]
                cur.execute("SELECT UNIX_TIMESTAMP(row_start) u FROM wiki_snapshot FOR SYSTEM_TIME ALL WHERE build_id=%s "
                            "AND wiki_id=%s AND page_id=%s AND row_start=%s", (build_id, wiki_id, page_id, first["row_start"]))
                epoch = (first["row_start"] - datetime(1970, 1, 1)).total_seconds()
                expect("UTC storage", float(cur.fetchone()["u"]), epoch)
                utc_checked = True
            for c in cov:
                s, e = c["start"], c["end"]
                if s not in cov_ends:
                    expect(f"{page_id} before coverage {s}", _as_of(cur, build_id, wiki_id, page_id, s - US), None)
                if e not in cov_starts:
                    expect(f"{page_id} at coverage end {e}", _as_of(cur, build_id, wiki_id, page_id, e), None)
                for iv in c["intervals"]:
                    for t in (iv.start, iv.end - US):
                        got = _as_of(cur, build_id, wiki_id, page_id, t)
                        expect(f"{page_id} interval {iv.revision_id} @ {t}", got, iv.revision_id)
                        expect(f"{page_id} archive @ {t}", _archive(cur, wiki_id, page_id, t), got)
                span = (e - s).total_seconds()
                for _ in range(samples_per_window):
                    t = s + timedelta(seconds=rng.random() * span)
                    t = t.replace(microsecond=rng.randrange(0, 1_000_000))
                    if t >= e:
                        continue
                    expect(f"{page_id} sample @ {t}", _as_of(cur, build_id, wiki_id, page_id, t),
                           _archive(cur, wiki_id, page_id, t))
    return {"ok": not failures, "checks": checks, "failures": failures}


def publish(conn, build_id: int) -> None:
    """Atomic switch of the active build."""
    conn.begin()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM wiki_build WHERE build_id=%s FOR UPDATE", (build_id,))
            st = cur.fetchone()
            if not st or st["status"] not in ("validated", "superseded", "published"):
                raise BuildError(f"build {build_id} is not validated")
            cur.execute("UPDATE wiki_build SET status='superseded' WHERE status='published' AND build_id<>%s", (build_id,))
            cur.execute("UPDATE wiki_build SET status='published', published_at=UTC_TIMESTAMP(6) WHERE build_id=%s", (build_id,))
            cur.execute("INSERT INTO wiki_active_build (id, build_id) VALUES (1,%s) ON DUPLICATE KEY UPDATE build_id=VALUES(build_id)",
                        (build_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
