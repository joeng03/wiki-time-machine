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
