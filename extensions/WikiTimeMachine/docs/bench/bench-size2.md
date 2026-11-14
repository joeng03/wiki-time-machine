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
