"""`python -m wtm.cli <command>` -- ingestion, projection and maintenance CLI (importer credentials)."""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime

from .config import settings
from .db import apply_schema, connect, server_capabilities
from .embed import get_embedder
from .timeutil import parse_utc


def _win(s: str) -> tuple[datetime, datetime]:
    a, b = s.split("/")
    return parse_utc(a, date_only_end_of_day=False)[0], parse_utc(b, date_only_end_of_day=False)[0]


def cmd_doctor(args):
    with connect("reader") as conn:
        caps = server_capabilities(conn)
    print(json.dumps(caps, indent=2))
    ok = caps["version_ok"] and caps["vector_supported"] and caps["insert_history_supported"]
    if args.check_insert_history:
        conn = connect("importer", insert_history=True)
        with conn.cursor() as cur:
            cur.execute("SELECT @@system_versioning_insert_history ih")
            print("importer insert_history:", cur.fetchone()["ih"])
        conn.close()
    sys.exit(0 if ok else 1)


def cmd_migrate(args):
    conn = connect("importer", database=args.database)
    apply_schema(conn, os.path.join(settings().sql_dir, "wtm_schema.sql"))
    print(f"schema applied to {args.database or settings().db_name}")


def cmd_fetch(args):
    from .fetch import WikiApi, capture_article
    api = WikiApi(settings().user_agent, host=args.host, min_interval=args.min_interval)
    windows = [_win(w) for w in args.window]
    m = capture_article(api, args.title, windows, args.out, rvlimit=args.rvlimit, max_api_pages=args.max_api_pages)
    print(json.dumps({"page": m["page"], "windows": m["windows"], "requests": m["source"]["requests"], "out": args.out}, indent=2))
    if any(w["status"] != "complete" for w in m["windows"]):
        print("WARNING: some windows are incomplete and will not be published", file=sys.stderr)


def cmd_load(args):
    from .fetch import read_capture
    from .staging import load_records
    conn = connect("importer")
    paths = sorted(p for pat in args.capture for p in glob.glob(pat))
    if not paths:
        sys.exit("no capture files matched")
    for p in paths:
        stats = load_records(conn, read_capture(p))
        print(p, json.dumps({k: v for k, v in stats.items() if k != "manifest"}))


def cmd_build(args):
    from .projection import build
    conn = connect("importer", insert_history=True)
    res = build(conn, get_embedder(args.embedder or settings().embedder))
    print(json.dumps({k: v for k, v in res.items() if k != "validation"} | (
        {"checks": res["validation"]["checks"]} if "validation" in res else {}), default=str, indent=2))


def cmd_import_parquet(args):
    from .datasets import m_versions_records
    from .staging import load_records
    conn = connect("importer")
    stats = load_records(conn, m_versions_records(args.file, set(args.page_id), sums_path=args.sha256sums))
    print(json.dumps({k: v for k, v in stats.items() if k != "manifest"}, default=str))


def cmd_corpus(args):
    from .datasets import load_corpus
    conn = connect("importer")
    if args.reset:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE corpus_passage")
    print(json.dumps(load_corpus(conn, args.file, get_embedder(args.embedder or settings().embedder),
                                 limit_articles=args.limit, max_passages=args.max_passages)))


def cmd_claims(args):
    """Extract tenure claims for the active build and publish the bitemporal projection."""
    import re
    from .api import _llm
    from .claims import INFOBOX_EXTRACTOR, extract_for_build, llm_extractor_id, project_claims
    from .snapshots import pinned_build
    conn = connect("importer", insert_history=True)
    build_id = pinned_build(conn)
    with conn.cursor() as cur:
        if args.page_id:
            pages = [(args.wiki, p) for p in args.page_id]
        else:  # pages whose snapshot revisions carry an officeholder-style infobox
            cur.execute("SELECT DISTINCT s.wiki_id, s.page_id, r.wikitext FROM wiki_snapshot FOR SYSTEM_TIME ALL s JOIN wiki_revision r "
                        "ON r.wiki_id=s.wiki_id AND r.revision_id=s.revision_id WHERE s.build_id=%s AND s.content_available", (build_id,))
            pat = re.compile(r"\{\{\s*Infobox[ _](official post|officeholder|political post)", re.I)
            pages = sorted({(r["wiki_id"], r["page_id"]) for r in cur.fetchall() if pat.search(r["wikitext"] or "")})
    if args.import_cache:
        from .claims import import_extraction_cache
        print("imported LLM extraction cache:", json.dumps(import_extraction_cache(conn, args.import_cache)))
    llm = _llm() if args.llm else None
    if llm is not None and hasattr(llm, "extra_body"):
        # Extraction is copy-and-classify under a strict schema; reasoning mostly burns the token budget.
        llm.extra_body = {**llm.extra_body, **json.loads(os.environ.get(
            "WTM_LLM_EXTRACT_EXTRA_BODY", '{"chat_template_kwargs": {"enable_thinking": false}}'))}
    model = settings().llm_model if llm else ""
    print(f"build {build_id}: claim pages {pages}; LLM extractor {'on: ' + model if llm else 'off'}")
    stats = extract_for_build(conn, build_id, pages, llm=llm, llm_model=model, concurrency=args.concurrency)
    print(json.dumps(stats))
    extractors = [INFOBOX_EXTRACTOR] + ([llm_extractor_id(model)] if llm else [])
    if not llm and args.use_cached_llm:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT extractor FROM wiki_claim_extraction WHERE status='ok'")
            cached = [r["extractor"] for r in cur.fetchall()]
        if cached:
            # map cached (fixture) LLM claims onto revisions without calling a model
            from .claims import extract_for_build as _efb

            class _CacheOnly:
                structured = False

                def __call__(self, *a, **k):
                    raise RuntimeError("cache-only mode: no model configured")
            for ext in cached:
                m = ext.split(":")[1]
                _efb(conn, build_id, pages, llm=_CacheOnly(), llm_model=m, concurrency=1, log=lambda *_: None)
                extractors.append(ext)
            print("using cached LLM extractions:", cached)
    if args.export_cache:
        from .claims import export_extraction_cache
        print("exported", export_extraction_cache(conn, args.export_cache), "LLM extraction results to", args.export_cache)
    res = project_claims(conn, build_id, extractors)
    print(json.dumps({k: v for k, v in res.items() if k != "validation"} | ({"checks": res["validation"]["checks"]} if "validation" in res else {})))


def cmd_bench(args):
    from .bench import run, to_markdown
    conn = connect("reader")
    r = run(conn, get_embedder(args.embedder or settings().embedder), args.label, n=args.n)
    md = to_markdown(r)
    print(md)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md)
        with open(args.out.rsplit(".", 1)[0] + ".json", "w", encoding="utf-8") as fh:
            json.dump(r, fh, indent=2, default=str)


def cmd_verify_links(args):
    """Check that stored revisions match what the permanent oldid links serve (network)."""
    import hashlib
    from .fetch import WikiApi
    api = WikiApi(settings().user_agent, min_interval=1.0)
    conn = connect("reader")
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT s.wiki_id, s.revision_id, r.text_sha256 FROM wiki_snapshot FOR SYSTEM_TIME ALL s "
                    "JOIN wiki_revision r ON r.wiki_id=s.wiki_id AND r.revision_id=s.revision_id "
                    "JOIN wiki_active_build a ON a.build_id=s.build_id WHERE s.content_available AND s.wiki_id='enwiki' "
                    "ORDER BY s.revision_id LIMIT %s", (args.limit,))
        rows = cur.fetchall()
    bad = 0
    for i in range(0, len(rows), 25):
        part = rows[i:i + 25]
        data = api.get({"action": "query", "prop": "revisions", "revids": "|".join(str(r["revision_id"]) for r in part),
                        "rvprop": "ids|content", "rvslots": "main"})
        live = {}
        for p in data["query"]["pages"]:
            for rv in p.get("revisions", []):
                c = rv["slots"]["main"].get("content")
                live[rv["revid"]] = hashlib.sha256(c.encode()).hexdigest() if c is not None else None
        for r in part:
            ok = live.get(r["revision_id"]) == r["text_sha256"]
            bad += not ok
            print(f"oldid={r['revision_id']}: {'OK' if ok else 'MISMATCH'}")
    print(f"{len(rows) - bad}/{len(rows)} permanent links match stored text")
    sys.exit(1 if bad else 0)


def main(argv=None):
    p = argparse.ArgumentParser(prog="wtm")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("doctor"); s.add_argument("--check-insert-history", action="store_true"); s.set_defaults(f=cmd_doctor)
    s = sub.add_parser("migrate"); s.add_argument("--database"); s.set_defaults(f=cmd_migrate)
    s = sub.add_parser("fetch", help="capture one article's revision windows from the Action API")
    s.add_argument("--title", required=True)
    s.add_argument("--window", action="append", required=True, help="START/END in UTC, e.g. 2023-04-08T00:00:00Z/2023-04-22T00:00:00Z")
    s.add_argument("--out", required=True)
    s.add_argument("--host", default="en.wikipedia.org")
    s.add_argument("--rvlimit", type=int, default=20)
    s.add_argument("--max-api-pages", type=int, default=100)
    s.add_argument("--min-interval", type=float, default=1.0)
    s.set_defaults(f=cmd_fetch)
    s = sub.add_parser("load", help="stage capture files (idempotent)"); s.add_argument("capture", nargs="+"); s.set_defaults(f=cmd_load)
    s = sub.add_parser("build", help="normalize, embed, project SVT, validate and publish"); s.add_argument("--embedder"); s.set_defaults(f=cmd_build)
    s = sub.add_parser("import-parquet", help="stage pages from an organizer M versions-NN.parquet")
    s.add_argument("--file", required=True); s.add_argument("--page-id", type=int, action="append", required=True)
    s.add_argument("--sha256sums"); s.set_defaults(f=cmd_import_parquet)
    s = sub.add_parser("corpus", help="load organizer S articles parquet into corpus_passage")
    s.add_argument("--file", required=True); s.add_argument("--limit", type=int); s.add_argument("--max-passages", type=int, default=6)
    s.add_argument("--reset", action="store_true"); s.add_argument("--embedder"); s.set_defaults(f=cmd_corpus)
    s = sub.add_parser("claims", help="bitemporal tenure claims for the active build")
    s.add_argument("--wiki", default="enwiki"); s.add_argument("--page-id", type=int, action="append")
    s.add_argument("--llm", action=argparse.BooleanOptionalAction, default=True, help="use the configured LLM extractor if any")
    s.add_argument("--concurrency", type=int, default=4)
    s.add_argument("--import-cache", help="load a portable LLM extraction cache (fixture) before extracting")
    s.add_argument("--export-cache", help="write the validated LLM extraction cache to a fixture file")
    s.add_argument("--use-cached-llm", action=argparse.BooleanOptionalAction, default=True,
                   help="without a model, still project cached LLM claims")
    s.set_defaults(f=cmd_claims)
    s = sub.add_parser("bench"); s.add_argument("--label", required=True); s.add_argument("--n", type=int, default=200)
    s.add_argument("--out"); s.add_argument("--embedder"); s.set_defaults(f=cmd_bench)
    s = sub.add_parser("verify-links"); s.add_argument("--limit", type=int, default=50); s.set_defaults(f=cmd_verify_links)
    args = p.parse_args(argv)
    args.f(args)


if __name__ == "__main__":
    main()
