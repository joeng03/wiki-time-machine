# Benchmarks (2026-11-14, MacBook, Docker Desktop, mariadb:11.8.9, no LLM calls)

Two data sizes. History corpus: real enwiki revisions (3 articles) + one fiwiki page from the organizer M
dataset in size 2. Corpus for ANN: English articles of the organizer S dataset (FineWiki Aug 2025), up to 6
passages per article, MiniLM 384-d, `VECTOR INDEX … M=16 DISTANCE=cosine`. Latencies are SQL round-trips
measured from the service container (`wtm bench`); query embedding (~4 ms warm) and model generation are
excluded and reported separately by `/ask` as `timing_ms`.

Observations
* SVT `AS OF` snapshot selection: sub-millisecond p50, range scan on the primary key.
* Revision-scoped retrieval: the optimizer always plans the membership join (`wiki_revision_chunk` PK `ref`
  → `wiki_chunk` `eq_ref` + filesort over ~13–27 chunks); the HNSW index is not used for the eligibility-filtered
  query, so it is effectively exact (recall 1.0). We keep exact ranking for `/ask` by design.
* Corpus-wide topic search: plan uses `idx_corpus_embedding` (`type=index`, rows=10). ANN p50 0.5–0.6 ms vs.
  exact scan 161 ms (3.9 k passages) → 542 ms (13.2 k passages); recall@10 vs. exact 0.875 → 0.90.
* Note the earlier unfiltered spike test: `WHERE`+ANN filters *after* the index top-K and can underfill —
  the reason a global ANN top-K + date filter is never used for answers.

## Benchmark: size-1: 3 articles history + 1,000 corpus articles

Embedding model: `sentence-transformers/all-MiniLM-L6-v2@1110a243fdf4/onnx-meanpool-l2`

| table | rows |
|---|---|
| wiki_revision | 217 |
| wiki_snapshot_all_versions | 434 |
| wiki_chunk | 399 |
| wiki_revision_chunk | 2,985 |
| corpus_passage | 3,897 |

| query | n | p50 ms | p95 ms | max ms | notes |
|---|---|---|---|---|---|
| snapshot_as_of | 200 | 0.23 | 0.42 | 1.98 |  |
| revision_exact_topk | 100 | 1.12 | 3.32 | 17.2 |  |
| revision_ann_with_predicate | 100 | 0.95 | 3.08 | 19.49 | recall_at_5_vs_exact=1.0, underfilled_or_missed=0 |
| corpus_ann_top10 | 40 | 0.51 | 3.47 | 15.32 | recall_at_10_vs_exact=0.875 |
| corpus_exact_top10 | 8 | 161.3 | 183.29 | 183.29 |  |

**EXPLAIN snapshot_plan**

| table | type | key | rows | Extra |
|---|---|---|---|---|
| wiki_snapshot | range | PRIMARY | 88 | Using where |

**EXPLAIN revision_exact_plan**

| table | type | key | rows | Extra |
|---|---|---|---|---|
| rc | ref | PRIMARY | 13 | Using where; Using temporary; Using filesort |
| c | eq_ref | PRIMARY | 1 |  |

**EXPLAIN revision_ann_plan**

| table | type | key | rows | Extra |
|---|---|---|---|---|
| rc | ref | PRIMARY | 13 | Using where; Start temporary; Using temporary; Using filesort |
| c | eq_ref | PRIMARY | 1 | End temporary |

**EXPLAIN corpus_ann_plan**

| table | type | key | rows | Extra |
|---|---|---|---|---|
| corpus_passage | index | idx_corpus_embedding | 10 |  |

**EXPLAIN corpus_exact_plan**

| table | type | key | rows | Extra |
|---|---|---|---|---|
| corpus_passage | ALL | None | 3126 | Using filesort |

## Benchmark: size-2: 4 articles history + 5,254 corpus articles

Embedding model: `sentence-transformers/all-MiniLM-L6-v2@1110a243fdf4/onnx-meanpool-l2`

| table | rows |
|---|---|
| wiki_revision | 245 |
| wiki_snapshot_all_versions | 679 |
| wiki_chunk | 432 |
| wiki_revision_chunk | 3,052 |
| corpus_passage | 13,248 |

| query | n | p50 ms | p95 ms | max ms | notes |
|---|---|---|---|---|---|
| snapshot_as_of | 200 | 0.39 | 1.56 | 3.53 |  |
| revision_exact_topk | 100 | 1.28 | 1.85 | 4.53 |  |
| revision_ann_with_predicate | 100 | 1.02 | 1.6 | 2.14 | recall_at_5_vs_exact=1.0, underfilled_or_missed=0 |
| corpus_ann_top10 | 40 | 0.62 | 17.73 | 34.91 | recall_at_10_vs_exact=0.9 |
| corpus_exact_top10 | 8 | 542.15 | 642.21 | 642.21 |  |

**EXPLAIN snapshot_plan**

| table | type | key | rows | Extra |
|---|---|---|---|---|
| wiki_snapshot | range | PRIMARY | 88 | Using where |

**EXPLAIN revision_exact_plan**

| table | type | key | rows | Extra |
|---|---|---|---|---|
| rc | ref | PRIMARY | 13 | Using where; Using temporary; Using filesort |
| c | eq_ref | PRIMARY | 1 |  |

**EXPLAIN revision_ann_plan**

| table | type | key | rows | Extra |
|---|---|---|---|---|
| rc | ref | PRIMARY | 13 | Using where; Start temporary; Using temporary; Using filesort |
| c | eq_ref | PRIMARY | 1 | End temporary |

**EXPLAIN corpus_ann_plan**

| table | type | key | rows | Extra |
|---|---|---|---|---|
| corpus_passage | index | idx_corpus_embedding | 10 |  |

**EXPLAIN corpus_exact_plan**

| table | type | key | rows | Extra |
|---|---|---|---|---|
| corpus_passage | ALL | None | 10892 | Using filesort |
