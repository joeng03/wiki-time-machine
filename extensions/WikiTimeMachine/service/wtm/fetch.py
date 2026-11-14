"""Wikimedia Action API fetcher: one article, bounded coverage windows, full continuation.

Writes a "capture" file (JSONL, gzip) that is both the reproducible demo fixture and the input of
`wtm load`. Fixture capture and live import use the same public, anonymous API access level.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import requests

from . import __version__
from .timeutil import api_ts, iso, parse_api_ts

DEFAULT_HOST = "en.wikipedia.org"
WIKI_IDS = {"en.wikipedia.org": "enwiki", "fi.wikipedia.org": "fiwiki"}


class ApiError(RuntimeError):
    pass


class WikiApi:
    def __init__(self, user_agent: str, host: str = DEFAULT_HOST, session: requests.Session | None = None,
                 min_interval: float = 1.0, max_retries: int = 6, timeout: float = 30.0,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if not user_agent or "(" not in user_agent:
            raise ApiError("WTM_USER_AGENT must be set to a descriptive, contactable User-Agent, e.g. "
                           "'WikiTimeMachine/0.1 (https://your.site; you@example.org)'")
        self.host = host
        self.endpoint = f"https://{host}/w/api.php"
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip"})
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self.sleep = sleep
        self._last = 0.0
        self.request_log: list[dict] = []

    def get(self, params: dict[str, Any]) -> dict:
        full = {**params, "format": "json", "formatversion": "2", "maxlag": "5"}
        for attempt in range(self.max_retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.session.get(self.endpoint, params=full, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise ApiError(f"network error: {exc}") from exc
                self.sleep(min(2 ** attempt, 30))
                continue
            retry_after = r.headers.get("Retry-After")
            if r.status_code in (429, 503) or (r.status_code == 200 and _is_maxlag(r)):
                if attempt >= self.max_retries:
                    raise ApiError(f"gave up after {attempt + 1} attempts (HTTP {r.status_code})")
                self.sleep(min(float(retry_after) if retry_after and retry_after.isdigit() else 2 ** (attempt + 1), 120))
                continue
            if r.status_code != 200:
                raise ApiError(f"HTTP {r.status_code}")
            data = r.json()
            if "error" in data:
                raise ApiError(f"API error {data['error'].get('code')}: {data['error'].get('info')}")
            self.request_log.append({k: v for k, v in full.items() if k != "continue"})
            return data
        raise ApiError("unreachable")


def _is_maxlag(r: requests.Response) -> bool:
    try:
        return r.json().get("error", {}).get("code") == "maxlag"
    except ValueError:
        return False


def resolve_title(api: WikiApi, title: str) -> dict:
    data = api.get({"action": "query", "titles": title, "redirects": "1"})
    q = data.get("query", {})
    page = (q.get("pages") or [{}])[0]
    if page.get("missing") or "pageid" not in page:
        raise ApiError(f"page not found: {title!r}")
    if page.get("ns") != 0:
        raise ApiError(f"only main-namespace articles are supported (ns={page.get('ns')})")
    return {"page_id": page["pageid"], "title": page["title"], "ns": page["ns"],
            "requested_title": title, "redirects": q.get("redirects", [])}


def _parse_revision(rev: dict, wiki_id: str, page_id: int) -> dict:
    slot = (rev.get("slots") or {}).get("main") or {}
    hidden = bool(rev.get("texthidden") or slot.get("texthidden") or slot.get("textmissing")
                  or rev.get("suppressed"))
    text = None if hidden else slot.get("content")
    model = slot.get("contentmodel")
    available = text is not None and model in (None, "wikitext")
    return {
        "kind": "revision", "wiki_id": wiki_id, "page_id": page_id,
        "revision_id": int(rev["revid"]), "parent_id": int(rev.get("parentid") or 0) or None,
        "timestamp": rev["timestamp"], "content_model": model, "content_available": available,
        "wikitext": text if available else None,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if available else None,
    }


REV_PROPS = "ids|timestamp|content|contentmodel"


def fetch_window(api: WikiApi, wiki_id: str, page_id: int, start: datetime, end: datetime,
                 rvlimit: int = 20, max_api_pages: int = 100) -> tuple[list[dict], dict]:
    """Fetch [start, end) forward with full continuation + the predecessor strictly before start.

    Returns (revisions, window_record). On any error or cap the window is marked 'failed' and must
    not be published; already-fetched revisions are still returned for the archive.
    """
    if start >= end:
        raise ValueError("window start must be before end")
    if start.microsecond or end.microsecond:
        raise ValueError("window boundaries must be whole seconds (API precision)")
    revisions: dict[int, dict] = {}
    window = {"kind": "window", "wiki_id": wiki_id, "page_id": page_id,
              "coverage_start": iso(start), "coverage_end": iso(end), "status": "failed",
              "source": "api", "predecessor_ids": None, "revision_count": 0, "api_pages": 0,
              "error": None}
    try:
        # Forward pass: rvstart/rvend are inclusive at second precision -> end - 1s.
        params = {"action": "query", "prop": "revisions", "pageids": str(page_id), "rvprop": REV_PROPS,
                  "rvslots": "main", "rvdir": "newer", "rvstart": api_ts(start),
                  "rvend": api_ts(end - timedelta(seconds=1)), "rvlimit": str(rvlimit)}
        cont: dict = {}
        while True:
            if window["api_pages"] >= max_api_pages:
                raise ApiError(f"page cap of {max_api_pages} API pages reached before continuation was exhausted")
            data = api.get({**params, **cont})
            window["api_pages"] += 1
            page = (data.get("query", {}).get("pages") or [{}])[0]
            for rev in page.get("revisions", []):
                rec = _parse_revision(rev, wiki_id, page_id)
                ts = parse_api_ts(rec["timestamp"])
                if start <= ts < end:
                    revisions[rec["revision_id"]] = rec
            if "continue" not in data:
                break
            cont = data["continue"]  # send the complete continuation object back
        # Predecessor: strictly before the window start (separate backward query).
        data = api.get({"action": "query", "prop": "revisions", "pageids": str(page_id), "rvprop": REV_PROPS,
                        "rvslots": "main", "rvdir": "older", "rvstart": api_ts(start - timedelta(seconds=1)),
                        "rvlimit": "5"})
        window["api_pages"] += 1
        page = (data.get("query", {}).get("pages") or [{}])[0]
        preds = [_parse_revision(r, wiki_id, page_id) for r in page.get("revisions", [])]
        preds = [p for p in preds if parse_api_ts(p["timestamp"]) < start and p["revision_id"] not in revisions]
        pred_ids: list[int] = []
        if preds:
            latest = max(parse_api_ts(p["timestamp"]) for p in preds)
            # keep every revision sharing the latest pre-window second (same-second ties)
            for p in preds:
                if parse_api_ts(p["timestamp"]) == latest:
                    revisions[p["revision_id"]] = p
                    pred_ids.append(p["revision_id"])
        window["predecessor_ids"] = sorted(pred_ids)
        window["revision_count"] = sum(1 for r in revisions.values() if start <= parse_api_ts(r["timestamp"]) < end)
        window["status"] = "complete"
    except (ApiError, KeyError, ValueError) as exc:
        window["error"] = str(exc)
        window["status"] = "failed"
    return sorted(revisions.values(), key=lambda r: (r["timestamp"], r["revision_id"])), window


def capture_article(api: WikiApi, title: str, windows: list[tuple[datetime, datetime]], out_path: str,
                    rvlimit: int = 20, max_api_pages: int = 100) -> dict:
    wiki_id = WIKI_IDS.get(api.host, api.host)
    page = resolve_title(api, title)
    all_revs: dict[int, dict] = {}
    window_recs = []
    for s, e in windows:
        revs, w = fetch_window(api, wiki_id, page["page_id"], s, e, rvlimit=rvlimit, max_api_pages=max_api_pages)
        window_recs.append(w)
        for r in revs:
            all_revs[r["revision_id"]] = r
    ua = api.session.headers.get("User-Agent", "")
    manifest = {
        "kind": "manifest", "format": "wtm-capture/1", "tool": f"wtm {__version__}",
        "fetched_at": iso(datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)),
        "source": {"endpoint": api.endpoint, "access": "public anonymous Action API",
                   "user_agent_product": ua.split("(")[0].strip(), "requests": len(api.request_log),
                   "request_params": api.request_log},
        "wiki_id": wiki_id, "page": page,
        "windows": [{k: w[k] for k in ("coverage_start", "coverage_end", "status", "revision_count",
                                         "api_pages", "predecessor_ids", "error")} for w in window_recs],
        "attribution": {"text": f"Text from the English Wikipedia article \"{page['title']}\" by Wikipedia "
                                f"contributors, licensed CC BY-SA 4.0. Each revision: "
                                f"https://{api.host}/w/index.php?oldid=<revision_id>",
                        "license": "https://creativecommons.org/licenses/by-sa/4.0/"},
        "editor_identities": "omitted",
    }
    page_rec = {"kind": "page", "wiki_id": wiki_id, "page_id": page["page_id"], "title": page["title"],
                "source_host": api.host, "ns": page["ns"]}
    write_capture(out_path, manifest, [page_rec], window_recs, list(all_revs.values()))
    return manifest


def write_capture(path: str, manifest: dict, pages: list[dict], windows: list[dict], revisions: list[dict]) -> None:
    revisions = sorted(revisions, key=lambda r: (r["page_id"], r["timestamp"], r["revision_id"]))
    manifest = {**manifest, "revision_sha256": hashlib.sha256(
        "\n".join(f"{r['revision_id']}:{r.get('text_sha256')}" for r in revisions).encode()).hexdigest()}
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "wt", encoding="utf-8") as fh:
        for rec in [manifest, *pages, *windows, *revisions]:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


def read_capture(path: str):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)
