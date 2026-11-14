"""Benchmarks: row counts, query plans, snapshot / retrieval / ANN latency (no LLM calls)."""
from __future__ import annotations

import random
import statistics
import time
from datetime import timedelta

from .embed import Embedder, to_sql_vector
from .normalize import PARSER_VERSION
from .retrieval import CORPUS_ANN_SQL, CORPUS_EXACT_SQL, REVISION_ANN_SQL, REVISION_TOPK_SQL, explain
from .snapshots import SVT_SQL, pinned_build

QUERIES = [
    "status of the third reactor and commercial operation",
    "construction delays and cost overruns",
    "NATO membership application and accession",
    "library opening and architecture",
    "Winter War peace treaty",
    "Finnish independence declaration",
    "nuclear waste repository",
    "civil war in 1918",
]


def _pct(xs: list[float]) -> dict:
    xs = sorted(xs)
    if not xs:
        return {}
    return {"n": len(xs), "p50_ms": round(statistics.median(xs), 2),
            "p95_ms": round(xs[min(len(xs) - 1, int(len(xs) * 0.95))], 2), "max_ms": round(xs[-1], 2)}


def run(conn, embedder: Embedder, label: str, n: int = 200, seed: int = 11) -> dict:
    rng = random.Random(seed)
    out: dict = {"label": label, "embed_model": embedder.model_id}
    with conn.cursor() as cur:
        counts = {}
        for name, sql in [("wiki_revision", "SELECT COUNT(*) n FROM wiki_revision"),
                          ("wiki_snapshot_all_versions", "SELECT COUNT(*) n FROM wiki_snapshot FOR SYSTEM_TIME ALL"),
                          ("wiki_chunk", "SELECT COUNT(*) n FROM wiki_chunk"),
                          ("wiki_revision_chunk", "SELECT COUNT(*) n FROM wiki_revision_chunk"),
                          ("corpus_passage", "SELECT COUNT(*) n FROM corpus_passage")]:
            cur.execute(sql)
            counts[name] = cur.fetchone()["n"]
        out["counts"] = counts
        build_id = pinned_build(conn)
        cur.execute("SELECT wiki_id, page_id, coverage_start, coverage_end FROM wiki_coverage WHERE build_id=%s", (build_id,))
        cov = cur.fetchall()

        # 1) Snapshot selection via SVT AS OF.
        lat = []
        for _ in range(n):
            c = rng.choice(cov)
            span = (c["coverage_end"] - c["coverage_start"]).total_seconds()
            t = c["coverage_start"] + timedelta(seconds=rng.random() * span)
            t0 = time.perf_counter()
            cur.execute(SVT_SQL, (t, build_id, c["wiki_id"], c["page_id"]))
            cur.fetchall()
            lat.append((time.perf_counter() - t0) * 1000)
        out["snapshot_as_of"] = _pct(lat)
        out["snapshot_plan"] = explain(conn, SVT_SQL, (cov[0]["coverage_start"], build_id, cov[0]["wiki_id"], cov[0]["page_id"]))

        # 2) Revision-scoped exact retrieval vs ANN-with-predicate.
        cur.execute("SELECT DISTINCT s.wiki_id, s.revision_id FROM wiki_snapshot FOR SYSTEM_TIME ALL s "
                    "WHERE s.build_id=%s AND s.content_available", (build_id,))
        revs = cur.fetchall()
        qvecs = embedder.embed(QUERIES)
        exact_lat, ann_lat, recalls = [], [], []
        for _ in range(min(n, 100)):
            r = rng.choice(revs)
            q = to_sql_vector(qvecs[rng.randrange(len(QUERIES))])
            t0 = time.perf_counter()
            cur.execute(REVISION_TOPK_SQL, (q, r["wiki_id"], r["revision_id"], PARSER_VERSION, embedder.model_id, 5))
            exact = [x["chunk_id"] for x in cur.fetchall()]
            exact_lat.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            cur.execute(REVISION_ANN_SQL, (q, r["wiki_id"], r["revision_id"], q, 5))
            ann = [x["chunk_id"] for x in cur.fetchall()]
            ann_lat.append((time.perf_counter() - t0) * 1000)
            if exact:
                recalls.append(len(set(exact) & set(ann)) / len(exact))
        out["revision_exact_topk"] = _pct(exact_lat)
        out["revision_ann_with_predicate"] = {**_pct(ann_lat), "recall_at_5_vs_exact": round(statistics.mean(recalls), 3) if recalls else None,
                                              "underfilled_or_missed": sum(1 for x in recalls if x < 1.0)}
        r0 = revs[0]
        q0 = to_sql_vector(qvecs[0])
        out["revision_exact_plan"] = explain(conn, REVISION_TOPK_SQL, (q0, r0["wiki_id"], r0["revision_id"], PARSER_VERSION, embedder.model_id, 5))
        out["revision_ann_plan"] = explain(conn, REVISION_ANN_SQL, (q0, r0["wiki_id"], r0["revision_id"], q0, 5))

        # 3) Corpus-wide ANN (HNSW index) vs exact scan.
        if counts["corpus_passage"]:
            ann_lat, ex_lat, recalls = [], [], []
            for qi, qv in enumerate(qvecs):
                q = to_sql_vector(qv)
                for _ in range(5):
                    t0 = time.perf_counter()
                    cur.execute(CORPUS_ANN_SQL, (q, 10))
                    ann = [x["passage_id"] for x in cur.fetchall()]
                    ann_lat.append((time.perf_counter() - t0) * 1000)
                t0 = time.perf_counter()
                cur.execute(CORPUS_EXACT_SQL, (q, 10))
                exact = [x["passage_id"] for x in cur.fetchall()]
                ex_lat.append((time.perf_counter() - t0) * 1000)
                recalls.append(len(set(ann) & set(exact)) / 10)
            out["corpus_ann_top10"] = {**_pct(ann_lat), "recall_at_10_vs_exact": round(statistics.mean(recalls), 3)}
            out["corpus_exact_top10"] = _pct(ex_lat)
            out["corpus_ann_plan"] = explain(conn, CORPUS_ANN_SQL, (to_sql_vector(qvecs[0]), 10))
            out["corpus_exact_plan"] = explain(conn, CORPUS_EXACT_SQL, (to_sql_vector(qvecs[0]), 10))
    return out


def to_markdown(r: dict) -> str:
    lines = [f"## Benchmark: {r['label']}", "", f"Embedding model: `{r['embed_model']}`", "", "| table | rows |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in r["counts"].items()]
    lines += ["", "| query | n | p50 ms | p95 ms | max ms | notes |", "|---|---|---|---|---|---|"]
    for key in ("snapshot_as_of", "revision_exact_topk", "revision_ann_with_predicate", "corpus_ann_top10", "corpus_exact_top10"):
        if key in r and r[key]:
            v = r[key]
            notes = ", ".join(f"{k}={v[k]}" for k in v if k.startswith(("recall", "under")))
            lines.append(f"| {key} | {v.get('n')} | {v.get('p50_ms')} | {v.get('p95_ms')} | {v.get('max_ms')} | {notes} |")
    for key in ("snapshot_plan", "revision_exact_plan", "revision_ann_plan", "corpus_ann_plan", "corpus_exact_plan"):
        if key in r:
            lines += ["", f"**EXPLAIN {key}**", "", "| table | type | key | rows | Extra |", "|---|---|---|---|---|"]
            for p in r[key]:
                lines.append(f"| {p.get('table')} | {p.get('type')} | {p.get('key')} | {p.get('rows')} | {p.get('Extra') or ''} |")
    return "\n".join(lines) + "\n"
