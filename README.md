# Wikipedia Time Machine — MediaWiki extension (Junction × MariaDB, BOTH track)

Pick a Wikipedia article and two UTC moments, see the article **as it stood at each moment**, a
deterministic diff, and ask **“what changed?”** — answered only from those two revisions, with
permanent `oldid` links and the exact supporting excerpts.

* **Snapshot selection:** native MariaDB **system-versioned table** (`wiki_snapshot … WITH SYSTEM VERSIONING`)
  whose system period is **backfilled with the real Wikipedia edit timeline**
  (`system_versioning_insert_history`), queried with `FOR SYSTEM_TIME AS OF`.
* **Evidence retrieval:** native MariaDB **`VECTOR(384)` + `VECTOR INDEX … DISTANCE=cosine`**, ranked with
  `VEC_DISTANCE_COSINE` in SQL, restricted to the exact resolved revision on each side.
* **Bitemporal claims:** officeholder tenures extracted per revision (infobox + Nemotron over prose, with
  exact excerpts) in a MariaDB table with **both** an application-time `PERIOD FOR valid_period` and a
  backfilled system period: *“as of Wikipedia's revision at T, who did the article say held office on D?”*
* **Grounded answers:** Nemotron 3.5 Super (OpenAI-compatible vLLM endpoint) with schema-constrained,
  side-restricted citations, validated server-side; diff + evidence remain when the model is off or fails.
* **Real data:** public English Wikipedia revisions (Action API) for 5 articles, plus organizer datasets
  (MariaDB `mariadb-wikipedia-datasets`: **M** full-history Parquet adapter, **S** English corpus for ANN).

```
Browser ──same-origin──▶ MediaWiki 1.43.11 (Special:WikiTimeMachine, api.php?action=wtm{articles,snapshot,compare,ask,claims})
                               │ server-side PHP ServiceClient (internal URL, timeouts)
                               ▼
                         Python service (FastAPI, read-only DB user)  ◀── wtm CLI (importer user):
                               │                                          fetch → load → build/validate/publish
                               ▼
                         MariaDB 11.8.9  ─ schema `mediawiki` (MediaWiki's own)
                                         └ schema `wtm`: wiki_revision archive, wiki_snapshot (SVT),
                                           wiki_chunk (VECTOR INDEX), wiki_revision_chunk, corpus_passage,
                                           wiki_claim_snapshot (bitemporal: valid_period + SYSTEM_TIME) …
```

## Pinned versions

| component | version |
|---|---|
| MediaWiki core | git tag **1.43.11** (LTS) from `gerrit.wikimedia.org/r/mediawiki/core.git` (commit `0e3538fb0e48`) |
| Vector skin | `REL1_43` (commit `f885ee1914ee`) |
| PHP runtime | `mediawiki:1.43.11` Docker image (core checkout bind-mounted over it); composer `2.8.12` |
| MariaDB | `mariadb:11.8.9` (verified: VECTOR, VECTOR INDEX, `system_versioning_insert_history`) |
| Python service | Python 3.12, FastAPI 0.115.6, PyMySQL 1.1.1, mwparserfromhell 0.6.6, onnxruntime 1.20.1 (see `requirements.txt`) |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` @ `1110a243fdf4` (official ONNX weights, mean-pool + L2, 384-d, CPU) |
| LLM | `nemotron-3.5-super-vl` on the hackathon Verda/DataCrunch vLLM endpoint (any OpenAI-compatible endpoint works); optional |

## Quick start (tested commands)

Requirements: Docker + Compose v2, ~3 GB disk, network for the first setup (git, composer, model download).

```bash
cp .env.example .env            # then set the passwords (and WTM_USER_AGENT only for live fetches)
make setup                      # clones pinned core+skin, composer install, docker compose up, install.php, migrate
make doctor                     # MariaDB version / VECTOR / historical-insert capability check
make demo                       # loads the shipped real fixtures, builds + validates + publishes the SVT projection
open http://localhost:8080/index.php/Special:WikiTimeMachine
```

The demo works **without network** once setup has run (fixtures are committed; the MiniLM model is cached in the
`hfcache` volume after the first build). Without the model cached, `WTM_EMBEDDER=hash` runs the whole pipeline with
the deterministic test embedder (lower retrieval quality).

`make demo` also publishes the bitemporal claims: infobox claims are extracted locally and the shipped Nemotron
extraction cache (`fixtures/claims/`) is replayed, so no key is needed. With `WTM_LLM_*` set, `make claims`
re-extracts with the live model (≈7 min for the two officeholder articles) and refreshes that cache.

Other commands: `make test` (Python DB tests + PHPUnit), `make test-e2e` (through MediaWiki on real data),
`make fetch-demo` (live re-capture), `make corpus [LIMIT=1000]`, `make bench LABEL=x`, `make verify-links`.
Organizer M data: `docker compose exec service python -m wtm.cli import-parquet --file /downloads/m-2026-10/versions-01.parquet --sha256sums /downloads/m-2026-10/SHA256SUMS --page-id 186` then `wtm build`.

### Environment variables (`.env`, never committed)

| variable | purpose |
|---|---|
| `MARIADB_ROOT_PASSWORD`, `MW_DB_PASSWORD`, `MW_ADMIN_PASSWORD` | DB root, MediaWiki DB user, wiki admin |
| `WTM_IMPORTER_PASSWORD` | `wtm_importer`: writes + historical SVT inserts (CLI only) |
| `WTM_READER_PASSWORD` | `wtm_reader`: **SELECT-only** runtime user of the HTTP service |
| `WTM_USER_AGENT` | required for live Wikimedia fetches: descriptive + contact, per Wikimedia UA policy |
| `WTM_LLM_BASE_URL`, `WTM_LLM_MODEL`, `WTM_LLM_API_KEY` | optional model; for the hackathon endpoint: `https://containers.datacrunch.io/nemotron-3-5-super-vl-b300/v1`, `nemotron-3.5-super-vl`, key in `.env` only |
| `WTM_LLM_TIMEOUT` (120), `WTM_LLM_MAX_TOKENS` (6000), `WTM_LLM_STRUCTURED` (1), `WTM_LLM_EXTRA_BODY`, `WTM_LLM_EXTRACT_EXTRA_BODY` | request tuning; structured = per-request JSON schema; extraction disables reasoning by default |
| `WTM_EMBEDDER` (`minilm`\|`hash`), `WTM_SNAPSHOT_MODE` (`svt`\|`archive`) | embedder; labelled archive fallback if SVT were unavailable |

MediaWiki settings live in `docker/mediawiki/LocalSettings.wtm.php` (included from `LocalSettings.php`):
`wfLoadExtension( 'WikiTimeMachine' )`, `$wgWikiTimeMachineServiceUrl`, `$wgWikiTimeMachineServiceTimeout`,
`$wgWikiTimeMachineAskTimeout`, `$wgWikiTimeMachineExamples`.

## Demo (verified on the shipped real data)

**Olkiluoto Nuclear Power Plant** (page 1977274), first date **2021-12-15**, second date **2023-04-20**
(date-only = end of day UTC), question *“How did the article describe the status of Olkiluoto 3 at these two dates?”*

Direct link: `http://localhost:8080/index.php?title=Special:WikiTimeMachine&page=1977274&before=2021-12-15&after=2023-04-20`

| side | resolved revision | saved (UTC) | supporting excerpt (from the stored revision) |
|---|---|---|---|
| 2021-12-15 | [oldid=1059771803](https://en.wikipedia.org/w/index.php?oldid=1059771803) | 2021-12-11 14:52:42 | Lead: “Unit 3 is an EPR reactor and has been under construction since 2005. The start of commercial operation was originally planned for May 2009 but the project has been delayed and, As of August 2020, the latest estimate for start of regular production is February 2022.” · Unit 3: “On 8 December 2021 the company submitted its application to Finland's Radiation and Nuclear Safety Authority asking permission to start up Unit 3 …” |
| 2023-04-20 | [oldid=1150938986](https://en.wikipedia.org/w/index.php?oldid=1150938986) | 2023-04-20 22:43:10 | Lead: “… Construction of unit 3 began in 2005. Commercial operation began on 16 April 2023, after being originally scheduled for May 2009.” · Unit 3: “… First criticality of the OL3 EPR plant unit was reached on 21 December 2021 … The electricity production of Olkiluoto’s third nuclear power plant unit started on Saturday, 12 March 2022 …” |

Observed: the diff shows 9 changed / 5 added / 5 removed paragraphs; retrieval returns the lead and *Unit 3*
passages above among the top-5 on each side (generation disabled → evidence view). Other shipped examples
(buttons on the page): Finland–NATO relations (application window 17–19 May 2022 vs. accession day
4 Apr 2023 12:00–14:00 UTC), Helsinki Central Library Oodi (Nov 2018 – Jan 2019, including a large expansion on
12–13 Jan 2019 and its mass revert), and Erik Edner (fiwiki, from the organizer **M** dataset).

### Nemotron answer (live, 2026-11-14)

Same demo through MediaWiki with `nemotron-3.5-super-vl` (3/3 runs validated, 41–52 s each, almost all model time):

* **2021-12-15 (oldid 1059771803):** “Unit 3 was under construction and not yet online; estimates for start of regular
  production were February 2022 …” [A1][A3] · “On 8 December 2021, the company submitted an application to start up
  Unit 3 and begin initial testing.” [A5]
* **2023-04-20 (oldid 1150938986):** “Unit 3 reached first criticality on 21 December 2021, started electricity production
  on 12 March 2022, and began commercial operation on 16 April 2023.” [B1][B2]
* **What changed:** “from an under-construction reactor awaiting a 2022 startup to an operational unit that began
  commercial operation on 16 April 2023.” [A1][A3][A5][B1][B2]

Every `[id]` is checked against its side and links to that side's `oldid` with the stored excerpt.

### Bitemporal claims (verified on real data)

**Prime Minister of Finland**, “who held the office on 2023-06-22?”
(`…&page=231789&before=2023-06-15&after=2023-06-25&validat=2023-06-22`):

| Wikipedia as of | revision | answer | supporting excerpts |
|---|---|---|---|
| 2023-06-15 | oldid 1157413357 | **Sanna Marin**, 2019-12-10 → present (described as current) | infobox `incumbent = [[Sanna Marin]] / incumbentsince = 10 December 2019`; prose (Nemotron): “The incumbent prime minister is Sanna Marin of the Social Democratic Party …” |
| 2023-06-25 | oldid 1161435472 | **Petteri Orpo**, 2023-06-20 → present | infobox `incumbent = [[Petteri Orpo]] / incumbentsince = 20 June 2023`; prose: “The incumbent prime minister is Petteri Orpo of the National Coalition Party …” |

The history view (`FOR SYSTEM_TIME ALL`) replays real edits of 9 Dec 2019: “Saana Marin, since 7 Dec” →
“… 8 Dec” (one minute later) → spelling fixed to “Sanna” → start date settled on 10 Dec 2019. **President of
Finland** (`page=196964`, 2024-02-20 vs 2024-03-05, valid 2024-03-05): Niinistö (since 1 March 2012, current) →
Stubb (since 1 March 2024); asking about 2015 under Wikipedia's March 2024 view returns no claim, because that
revision no longer states Niinistö's tenure.

```sql
SELECT claim_id, office, holder, valid_from, valid_to, from_precision, to_precision, end_status, row_start, row_end
FROM wiki_claim_snapshot FOR SYSTEM_TIME AS OF CAST(? AS DATETIME(6))   -- when Wikipedia said it
WHERE claim_build_id = ? AND wiki_id = ? AND page_id = ?
  AND valid_from <= ? AND valid_to > ?                                   -- when the tenure applied
```

How claims are made and kept honest:
* **Extractors.** `infobox-v1` (deterministic: `{{Infobox official post}}` incumbent/incumbentsince/inaugural,
  `{{Infobox officeholder}}` office/term_start/term_end) and `llm:nemotron-3.5-super-vl:tenure-v1` over prose chunks
  that mention a year and the office, **one call per unique chunk** (cached), schema-constrained output.
* **Validation of model claims:** excerpt must be a verbatim substring of the chunk; holder must appear in it; every
  claimed date (year, and month/day at that precision) must be visible in it; office must match the page's office.
  On the real data: 40 accepted, 5 rejected (e.g. “Regent”, “head of state of White Finland” → other office).
* **Explicit uncertainty:** precision `day|month|year|unknown`; `end_status ended|ongoing|unknown`; unknown start
  `0001-01-01`; a *current* tenure is open-ended (`9999-12-31`) as of that revision; an *unstated* end covers only the
  stated start granule (never silently extended). Year/month precision covers the whole stated granule.
* **System time** comes from the published snapshot build: each claim is present exactly while the revision current in
  `wiki_snapshot` asserts it. Validation (106 checks) confirms, at both ends of every row, that the claim is visible
  `AS OF` that time **and** that the revision current at that time (via the SVT) asserts it; then the claim build is
  published atomically. Unchanged inputs reuse the published claim build.

## How the time travel works

1. **Ingest** (`wtm fetch`): resolve title → canonical page id (redirects), then per coverage window
   `[start, end)` a forward `rvdir=newer` query with full continuation, plus a separate backward query for the
   predecessor strictly before `start` (same-second predecessors kept, window-start duplicates removed).
   API error or page cap → window `failed`, never published. Hidden text → `content_available = false`.
   Bounded requests, `maxlag`, `Retry-After`, ≥1 s spacing, descriptive UA. Output: a gzip JSONL capture with a
   manifest (request parameters, attribution) — the same file is the offline fixture.
2. **Stage** (`wtm load`): idempotent on `(wiki_id, revision_id)`; identical repeats are counted, conflicting
   repeats raise; out-of-order input is detected; windows are keyed and upgraded only failed→complete.
3. **Build** (`wtm build`, importer session with `time_zone='+00:00'` and
   `SET SESSION system_versioning_insert_history = ON`): merge overlapping complete windows, check each window's
   recorded predecessor against the archive, compute half-open intervals (boundaries = coverage start + each
   distinct revision timestamp; same-second ties → greatest revision id; holes before page creation; closed at
   coverage end — **no open-ended current row**), normalize/chunk/embed only new unique chunks, insert explicit
   `row_start/row_end`, then **validate** (start and end−1 µs of every interval via `AS OF`, before/after coverage,
   overlap check, 25 random samples per window vs. an independent archive predecessor lookup, `TIMESTAMP(6)`
   precision, UTC storage via `UNIX_TIMESTAMP`). Only then the active build is switched in one transaction.
   An unchanged input fingerprint reuses the published build (no duplicate rows).
4. **Query**: every request pins the active `build_id` once, checks coverage, then
   ```sql
   SELECT revision_id, content_available, row_start, row_end
   FROM wiki_snapshot FOR SYSTEM_TIME AS OF CAST(? AS DATETIME(6))
   WHERE build_id = ? AND wiki_id = ? AND page_id = ?;
   ```
   The displayed time is the source revision timestamp from `wiki_revision` (an interval may start at the
   coverage boundary). `/timeline` uses `FOR SYSTEM_TIME FROM … TO …`.

## Retrieval and answers

* Text: wikitext → sections/paragraphs with mwparserfromhell (templates not expanded; a whitelist of inline
  templates such as `convert`, `as of`, `lang`, dates is rendered locally; refs/tables/files/categories dropped),
  stored separately from the raw wikitext. Chunks ≈ 200–350 tokens, embedded as `"<section>: <text>"`.
* Per side: `date → revision (SVT) → that revision's chunks (wiki_revision_chunk) → VEC_DISTANCE_COSINE ORDER BY … LIMIT k`
  — exact ranking over the eligible set, so a passage from another time can never be retrieved.
* Generation (optional): model sees labelled snippets (A* earlier, B* later) and must return JSON with
  per-side statements + snippet IDs. Every ID is validated against the correct side; excerpts and links are
  re-attached from the database; malformed output, wrong-side citations, uncited statements, timeouts and
  outages all fall back to the evidence + diff view. All text is rendered with `textContent`.

## Measured results

See `extensions/WikiTimeMachine/docs/benchmarks.md` (row counts, EXPLAIN plans, latency; no LLM calls).
Summary (SQL only, p50 / p95):

| | size 1 (217 revisions, 3.9 k corpus passages) | size 2 (245 revisions, 13.2 k corpus passages) |
|---|---|---|
| SVT `AS OF` snapshot selection | 0.23 / 0.42 ms | 0.39 / 1.56 ms |
| revision-scoped exact top-5 (`VEC_DISTANCE_COSINE`) | 1.12 / 3.32 ms | 1.28 / 1.85 ms |
| corpus ANN top-10 (HNSW index) | 0.51 / 3.47 ms, recall@10 0.875 | 0.62 / 17.7 ms, recall@10 0.90 |
| corpus exact top-10 (`IGNORE INDEX`) | 161 / 183 ms | 542 / 642 ms |

Warm `/ask` end to end without a model: resolve ≈7 ms, query embedding ≈4 ms, retrieval ≈5 ms;
`/compare` (two snapshots + diff) ≈30 ms. Current build (6 history pages): 286 intervals, 1,703 checks passed; bitemporal claim build: 21 rows, 106 checks. Benchmarks were measured before the two officeholder articles were added.

## Tests (all run on 2026-11-14)

| suite | command | result |
|---|---|---|
| Python unit + MariaDB integration (synthetic fixtures, hash embedder, mocked LLM) | `make test-db` | **41 passed** |
| PHPUnit (`TimeInput`) | `make test-php` | **11 tests, 14 assertions OK** |
| End-to-end through MediaWiki `api.php` on real cached data (incl. live Nemotron ask, bitemporal claims) | `make test-e2e` | **5 passed** |
| Offline reproduction from committed fixtures in a fresh database | `make demo` | identical: 286 intervals / 1,703 checks; 21 claim rows / 106 checks |
| Permanent links vs stored text (live Wikipedia) | `make verify-links` | **217/217 match** |
| Browser: headless Chrome on the special page (desktop + 390 px; claims panel; live Nemotron answer) | manual script | renders, no JS errors, no horizontal scroll |

Covered: just before / exactly at / after an edit; same-second ties; duplicate imports (build reused, no
duplicate rows); conflicting duplicates; partial fetches (unpublished); hidden text; out-of-window and
after-coverage queries; overlapping windows; page created inside a window; predecessor mismatch blocks
publication; atomic re-publish; reader user cannot write history; future passage never retrieved/cited;
shared text cites each side's own oldid; invalid/wrong-side/uncited model citations and malformed output
rejected; model outage; schema-constrained output, 400 fallback, empty/length-truncated content, trailing text;
bitemporal claims (precision and sentinel semantics, infobox extraction, model-claim validation, per-chunk cache,
AS OF + valid-time queries, assertion history, claim-build reuse); reversed/invalid dates; XSS in query string; continuation object sent back intact;
Retry-After; page cap; Parquet adapter (schema, provenance, parent-chain completeness, no editor columns).

## Incomplete / limitations (honest status)

* **Model latency:** the hackathon endpoint takes ≈20 s fixed overhead per call; `/ask` with generation takes 40–60 s
  (the UI shows evidence state and uses a 180 s timeout). Live-model e2e tests therefore need network and the key.
* **Bitemporal claims are a narrow domain:** officeholder tenures of the article's own office (2 articles). Tables
  (e.g. lists of former office holders) are not parsed; model-extracted claims inherit the article's wording
  (e.g. “will take office on 1 March 2024” becomes a start-only claim).
* The article-scoped retrieval does **not** use the HNSW index (optimizer chooses the membership join; we keep
  exact ranking by design). The ANN index is demonstrated and measured on corpus-wide topic search (`/search`).
* Rows of a build that fails validation stay in `wiki_snapshot` history (never published; removing them needs
  `DELETE HISTORY`). Builds are immutable and addressed by `build_id`.
* Fixtures were captured with a generic tool User-Agent (no personal contact was available); set your own
  `WTM_USER_AGENT` for further live fetches.
* Date inputs are HTML `datetime-local` controls labelled UTC; values are always sent with `Z`.
* English main-namespace wikitext only (plus the fiwiki organizer page). Templates are not expanded — use the
  oldid link for the complete original.

## Layout

```
docker-compose.yml, Makefile, .env.example, scripts/setup-mediawiki.sh
docker/mariadb/{init,conf}            DB users/schemas, UTC + secure_timestamp=NO
docker/mediawiki/LocalSettings.wtm.php
mediawiki/                            pinned upstream checkout (gitignored, recreated by setup)
extensions/WikiTimeMachine/
  extension.json, WikiTimeMachine.alias.php, i18n/, includes/ (SpecialPage, API modules, ServiceClient),
  modules/ (ResourceLoader JS/LESS), tests/phpunit/
  sql/wtm_schema.sql                  app schema (SVT + VECTOR)
  service/                            Python service + CLI (wtm/), tests/, tests_e2e/, Dockerfile
  fixtures/real (Wikipedia, CC BY-SA), fixtures/claims (Nemotron extraction cache), fixtures/synthetic (CI), docs/
```

Wikipedia text © Wikipedia contributors, CC BY-SA 4.0. Code: GPL-2.0-or-later (MediaWiki extension convention).
