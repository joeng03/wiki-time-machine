# Fixtures

## `real/` — REAL public English Wikipedia revision data (demo fixtures)

Captured with `wtm fetch` from the public, anonymous Wikimedia Action API (`action=query&prop=revisions`,
`rvprop=ids|timestamp|content|contentmodel`, `rvslots=main`, `formatversion=2`, full continuation, separate
backward query for the predecessor strictly before each window). Editor identities are not stored.

**Licence/attribution:** text by Wikipedia contributors, [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
Every revision is available at `https://en.wikipedia.org/w/index.php?oldid=<revision_id>`.
`wtm verify-links` re-fetched all 217 snapshot revisions on 2026-11-14 and every SHA-256 matched.

| file | article (page id) | fetched (UTC) | windows [start, end) | revisions in window | API pages | predecessor | sha256 (file) |
|---|---|---|---|---|---|---|---|
| finland-nato.jsonl.gz | Finland–NATO relations (52169647) | 2026-11-14T10:53:04Z | 2022-05-17T00:00:00Z → 2022-05-19T00:00:00Z | 23 | 4 | [1088175316] | 986dc22df4bae372… |
|  |  |  | 2023-04-04T12:00:00Z → 2023-04-04T14:00:00Z | 21 | 4 | [1148154569] |  |
| olkiluoto.jsonl.gz | Olkiluoto Nuclear Power Plant (1977274) | 2026-11-14T10:52:05Z | 2021-12-01T00:00:00Z → 2022-01-01T00:00:00Z | 11 | 3 | [1057015045] | 9972040334be1a06… |
|  |  |  | 2022-03-01T00:00:00Z → 2022-04-01T00:00:00Z | 10 | 2 | [1068858713] |  |
|  |  |  | 2023-04-08T00:00:00Z → 2023-04-22T00:00:00Z | 64 | 8 | [1136539634] |  |
| oodi.jsonl.gz | Helsinki Central Library Oodi (45223395) | 2026-11-14T10:54:05Z | 2018-11-01T00:00:00Z → 2019-01-20T00:00:00Z | 82 | 10 | [857748576] | 086efbe315bcf516… |

Reproduce (network): `make fetch-demo` (needs `WTM_USER_AGENT`). Load offline: `make demo`.

Format `wtm-capture/1`: gzip JSONL; line 1 manifest (source, request parameters, attribution), then `page`,
`window` and `revision` records. The same format is produced by the organizer-dataset adapter.

## `synthetic/` — SYNTHETIC deterministic CI fixtures (NOT Wikipedia data)

`temporal-isolation.jsonl`: a future passage that is highly relevant to the test question, shared text across
revisions, a same-second tie, hidden text and a failed (unpublished) window. Used by `tests/test_fixture_file.py`;
further synthetic cases are built inline in `tests/`.
