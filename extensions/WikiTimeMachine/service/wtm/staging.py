"""Idempotent staging of captured pages, revisions and coverage windows."""
from __future__ import annotations

import hashlib
import json
from typing import Iterable

from .timeutil import parse_api_ts, parse_utc


class ConflictError(RuntimeError):
    """The same (wiki_id, revision_id) arrived with different content/metadata."""


def window_key(wiki_id: str, page_id: int, start: str, end: str, source: str) -> str:
    return hashlib.sha256(f"{wiki_id}|{page_id}|{start}|{end}|{source}".encode()).hexdigest()


def load_records(conn, records: Iterable[dict]) -> dict:
    stats = {"pages": 0, "revisions_new": 0, "revisions_identical": 0, "windows_new": 0,
             "windows_existing": 0, "windows_upgraded": 0, "out_of_order": 0, "manifest": None}
    last_ts: dict[tuple, str] = {}
    with conn.cursor() as cur:
        for rec in records:
            kind = rec.get("kind")
            if kind == "manifest":
                stats["manifest"] = {k: rec.get(k) for k in ("format", "fetched_at", "wiki_id")}
            elif kind == "page":
                cur.execute(
                    "INSERT INTO wiki_page (wiki_id, page_id, title, source_host, namespace) VALUES (%s,%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE title=VALUES(title), source_host=VALUES(source_host)",
                    (rec["wiki_id"], rec["page_id"], rec["title"], rec["source_host"], rec.get("ns", 0)))
                stats["pages"] += 1
            elif kind == "revision":
                key = (rec["wiki_id"], rec["page_id"])
                if key in last_ts and rec["timestamp"] < last_ts[key]:
                    stats["out_of_order"] += 1
                last_ts[key] = max(rec["timestamp"], last_ts.get(key, ""))
                _upsert_revision(cur, rec, stats)
            elif kind == "window":
                _upsert_window(cur, rec, stats)
            else:
                raise ValueError(f"unknown record kind {kind!r}")
    return stats


def _ts(value: str):
    return parse_api_ts(value) if value.endswith("Z") and "T" in value and len(value) == 20 else parse_utc(value)[0]


def _upsert_revision(cur, rec: dict, stats: dict) -> None:
    ts = _ts(rec["timestamp"])
    cur.execute("SELECT page_id, parent_id, revision_ts, content_available, text_sha256 FROM wiki_revision "
                "WHERE wiki_id=%s AND revision_id=%s", (rec["wiki_id"], rec["revision_id"]))
    existing = cur.fetchone()
    if existing:
        same = (existing["page_id"] == rec["page_id"] and existing["revision_ts"] == ts
                and bool(existing["content_available"]) == bool(rec["content_available"])
                and existing["text_sha256"] == rec.get("text_sha256")
                and (existing["parent_id"] or None) == (rec.get("parent_id") or None))
        if not same:
            raise ConflictError(f"revision {rec['wiki_id']}:{rec['revision_id']} differs from the stored record")
        stats["revisions_identical"] += 1
        return
    cur.execute(
        "INSERT INTO wiki_revision (wiki_id, revision_id, page_id, parent_id, revision_ts, content_model, "
        "content_available, wikitext, text_sha256, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (rec["wiki_id"], rec["revision_id"], rec["page_id"], rec.get("parent_id"), ts, rec.get("content_model"),
         bool(rec["content_available"]), rec.get("wikitext"), rec.get("text_sha256"), rec.get("source", "api")))
    stats["revisions_new"] += 1


def _upsert_window(cur, rec: dict, stats: dict) -> None:
    start, end = _ts(rec["coverage_start"]), _ts(rec["coverage_end"])
    key = window_key(rec["wiki_id"], rec["page_id"], rec["coverage_start"], rec["coverage_end"], rec["source"])
    manifest = json.dumps({k: rec.get(k) for k in ("error", "api_pages")})
    cur.execute("SELECT window_id, status FROM wiki_fetch_window WHERE window_key=%s", (key,))
    existing = cur.fetchone()
    pred = json.dumps(rec.get("predecessor_ids")) if rec.get("predecessor_ids") is not None else None
    if existing:
        if existing["status"] != "complete" and rec["status"] == "complete":
            cur.execute("UPDATE wiki_fetch_window SET status='complete', predecessor_ids=%s, revision_count=%s, "
                        "api_pages=%s, manifest=%s WHERE window_id=%s",
                        (pred, rec.get("revision_count", 0), rec.get("api_pages", 0), manifest, existing["window_id"]))
            stats["windows_upgraded"] += 1
        else:
            stats["windows_existing"] += 1
        return
    cur.execute(
        "INSERT INTO wiki_fetch_window (wiki_id, page_id, coverage_start, coverage_end, status, source, "
        "predecessor_ids, revision_count, api_pages, manifest, window_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (rec["wiki_id"], rec["page_id"], start, end, rec["status"], rec["source"], pred,
         rec.get("revision_count", 0), rec.get("api_pages", 0), manifest, key))
    stats["windows_new"] += 1
