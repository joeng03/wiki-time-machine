"""Runtime resolution: requested UTC time -> revision, via the SVT projection of one pinned build."""
from __future__ import annotations

import json
from datetime import datetime

from .normalize import PARSER_VERSION
from .timeutil import iso


class NoActiveBuild(RuntimeError):
    pass


def pinned_build(conn) -> int:
    """Read the active build id ONCE per request; every query of the request uses this id."""
    with conn.cursor() as cur:
        cur.execute("SELECT build_id FROM wiki_active_build WHERE id=1")
        row = cur.fetchone()
    if not row:
        raise NoActiveBuild("no published build")
    return int(row["build_id"])


def oldid_url(host: str, revision_id: int) -> str:
    return f"https://{host}/w/index.php?oldid={revision_id}"


def page_info(conn, wiki_id: str, page_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT wiki_id, page_id, title, source_host FROM wiki_page WHERE wiki_id=%s AND page_id=%s",
                    (wiki_id, page_id))
        return cur.fetchone()


def coverage(conn, build_id: int, wiki_id: str, page_id: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT coverage_start, coverage_end FROM wiki_coverage WHERE build_id=%s AND wiki_id=%s AND page_id=%s "
                    "ORDER BY coverage_start", (build_id, wiki_id, page_id))
        return cur.fetchall()


SVT_SQL = ("SELECT revision_id, content_available, row_start, row_end FROM wiki_snapshot "
           "FOR SYSTEM_TIME AS OF CAST(%s AS DATETIME(6)) WHERE build_id=%s AND wiki_id=%s AND page_id=%s")

# Explicitly labelled fallback (only if SVT is unavailable): predecessor lookup over the archive,
# bounded by the same verified coverage metadata.
ARCHIVE_SQL = ("SELECT revision_id, content_available, revision_ts AS row_start, NULL AS row_end FROM wiki_revision "
               "WHERE wiki_id=%s AND page_id=%s AND revision_ts <= %s ORDER BY revision_ts DESC, revision_id DESC LIMIT 1")


def resolve(conn, build_id: int, wiki_id: str, page_id: int, at: datetime, mode: str = "svt",
            with_sections: bool = False) -> dict:
    page = page_info(conn, wiki_id, page_id)
    if not page:
        return {"status": "unknown_page", "requested_at": iso(at)}
    base = {"requested_at": iso(at), "resolution": mode, "wiki_id": wiki_id, "page_id": page_id, "title": page["title"]}
    with conn.cursor() as cur:
        cur.execute("SELECT coverage_start, coverage_end FROM wiki_coverage WHERE build_id=%s AND wiki_id=%s AND page_id=%s "
                    "AND coverage_start <= %s AND %s < coverage_end", (build_id, wiki_id, page_id, at, at))
        cov = cur.fetchone()
        if not cov:
            return {**base, "status": "not_covered", "message": "No covered snapshot for this time."}
        if mode == "svt":
            cur.execute(SVT_SQL, (at, build_id, wiki_id, page_id))
        else:
            cur.execute(ARCHIVE_SQL, (wiki_id, page_id, at))
        row = cur.fetchone()
        base["coverage"] = {"start": iso(cov["coverage_start"]), "end": iso(cov["coverage_end"])}
        if not row:
            return {**base, "status": "no_revision", "message": "The page had no revision at this time."}
        cur.execute("SELECT revision_ts, parent_id FROM wiki_revision WHERE wiki_id=%s AND revision_id=%s",
                    (wiki_id, row["revision_id"]))
        rev = cur.fetchone()
    out = {**base, "revision_id": int(row["revision_id"]), "source_timestamp": iso(rev["revision_ts"]),
           "parent_id": rev["parent_id"], "url": oldid_url(page["source_host"], row["revision_id"]),
           "interval": {"start": iso(row["row_start"]), "end": iso(row["row_end"])} if mode == "svt" else None}
    if not row["content_available"]:
        return {**out, "status": "unavailable", "message": "The text of this revision is hidden or unavailable."}
    out["status"] = "ok"
    if with_sections:
        out["sections"] = revision_sections(conn, wiki_id, row["revision_id"])
    return out


def revision_sections(conn, wiki_id: str, revision_id: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT sections FROM wiki_revision_text WHERE wiki_id=%s AND revision_id=%s AND parser_version=%s",
                    (wiki_id, revision_id, PARSER_VERSION))
        row = cur.fetchone()
    return json.loads(row["sections"]) if row else []


def list_articles(conn, build_id: int, q: str = "", limit: int = 50) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT p.wiki_id, p.page_id, p.title, p.source_host FROM wiki_page p "
                    "WHERE EXISTS (SELECT 1 FROM wiki_coverage c WHERE c.build_id=%s AND c.wiki_id=p.wiki_id AND c.page_id=p.page_id) "
                    "AND p.title LIKE %s ORDER BY p.title LIMIT %s", (build_id, f"%{q}%", limit))
        pages = cur.fetchall()
    for p in pages:
        p["windows"] = [{"start": iso(c["coverage_start"]), "end": iso(c["coverage_end"])}
                        for c in coverage(conn, build_id, p["wiki_id"], p["page_id"])]
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) n FROM wiki_snapshot FOR SYSTEM_TIME ALL WHERE build_id=%s AND wiki_id=%s AND page_id=%s",
                        (build_id, p["wiki_id"], p["page_id"]))
            p["snapshot_intervals"] = cur.fetchone()["n"]
    return pages


def timeline(conn, build_id: int, wiki_id: str, page_id: int, start: datetime, end: datetime) -> list[dict]:
    """Visible revision states over a range (FOR SYSTEM_TIME FROM ... TO ...)."""
    with conn.cursor() as cur:
        cur.execute("SELECT s.revision_id, s.content_available, s.row_start, s.row_end, r.revision_ts FROM wiki_snapshot "
                    "FOR SYSTEM_TIME FROM CAST(%s AS DATETIME(6)) TO CAST(%s AS DATETIME(6)) s "
                    "JOIN wiki_revision r ON r.wiki_id=s.wiki_id AND r.revision_id=s.revision_id "
                    "WHERE s.build_id=%s AND s.wiki_id=%s AND s.page_id=%s ORDER BY s.row_start",
                    (start, end, build_id, wiki_id, page_id))
        return [{"revision_id": r["revision_id"], "content_available": bool(r["content_available"]),
                 "start": iso(r["row_start"]), "end": iso(r["row_end"]), "source_timestamp": iso(r["revision_ts"])}
                for r in cur.fetchall()]
