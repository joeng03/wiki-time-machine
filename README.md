# Wikipedia Time Machine — MediaWiki extension (Junction × MariaDB, BOTH track)

**Upstream status:** independent MediaWiki extension prototype; not yet submitted
to or accepted by Wikimedia. See [the contribution plan](CONTRIBUTING.md) for the
Gerrit submission path and the separate requirements for deployment on Wikipedia.

**Live demo:** [WikiTimeMachine on Fly.io](https://wiki-time-machine-joeng03.fly.dev/index.php/Special:WikiTimeMachine).
Deployment and update instructions: [Fly.io deployment](docker/fly/README.md).

Read: [why it matters](#why-this-matters) · [architecture](#architecture) ·
[technical concepts](#technical-concepts) · [local setup](#quick-start-tested-commands) ·
[examples](#demo-verified-on-the-shipped-real-data) · [data and API](#data-model-and-api) ·
[Fly.io deployment](#deployment-on-flyio) · [results](#measured-results) ·
[limitations](#incomplete--limitations-honest-status) · [upstream contribution](#contributing-to-mediawiki-and-wikimedia).

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

## Why this matters

A historical question has two parts: finding relevant information and making sure that information
belongs to the requested moment. A passage can be highly relevant yet historically wrong for the
question. For example, an explanation of Olkiluoto 3's commercial operation in April 2023 must not be
used as evidence for what its Wikipedia article said in December 2021.

WikiTimeMachine makes time part of evidence selection. It first resolves the article revision at each
requested date, then searches only the passages belonging to that revision. The result is an inspectable
chain: **requested date → revision → passage → citation → answer**. The paragraph diff works without
an LLM, and each citation lets the reader inspect the original Wikipedia revision.

The officeholder examples go further: they separate **when an event applied** from **when a document
asserted it**. A June 15 article describing Sanna Marin as the current prime minister and a June 25
article describing Petteri Orpo as current can give different answers about June 22. The earlier answer
records the earlier article's assertion; it is not a claim that the article accurately predicted June 22.
Preserving that distinction helps explain corrections, delayed updates, and changes in knowledge.

For the MariaDB challenge, the contribution is the combination of native historical SQL, structured
claims, and vector similarity in one database. Revision membership and time coverage stay in relational
queries rather than being reconstructed from a separate vector store. This is a working demonstration
on a bounded dataset, with measured query plans and reproducible fixtures; it does not establish
Wikipedia-scale performance or guarantee the truth of generated answers.

The same design could support historical policy documents, changing product manuals, or research
archives. Those are possible applications of the architecture, not integrations implemented here.

## Architecture

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

| Component | Responsibility | Reason for the boundary |
|---|---|---|
| MediaWiki extension, PHP + ResourceLoader JavaScript/LESS | Special page, article/date controls, diff/evidence UI, MediaWiki API modules and messages | Integrates with MediaWiki's extension, configuration, permission and localisation mechanisms |
| FastAPI service | Resolve snapshots, calculate text diffs, embed questions, retrieve evidence, validate generated output | Keeps Python parsing and inference dependencies out of PHP |
| `wtm` command-line importer | Fetch/archive revisions, import Parquet, build embeddings and temporal projections, validate and publish | Expensive data preparation is an explicit operation rather than part of a web request |
| MariaDB | MediaWiki's own schema plus the separate `wtm` archive, temporal tables, vectors and publication metadata | Stores source provenance and retrieval eligibility together |
| MiniLM via ONNX Runtime | Turn text and questions into comparable vectors on CPU | Retrieval works without a paid embedding API or GPU |
| Optional Nemotron endpoint | Extract tenure claims during preparation and synthesize answers at query time | Adds language generation while preserving deterministic diffs and source evidence when unavailable |

Browser requests go to MediaWiki on the same origin. PHP calls FastAPI internally; the browser does
not receive database credentials or the model API key. HTTP query code opens database connections
with the SELECT-only reader account. Maintenance commands use the importer account. On the current
demo API machine both credentials are configured for maintenance, so this is a connection-level role
separation, not a separate isolated importer deployment.

### A request from start to finish

1. The user selects an article and two UTC moments. MediaWiki validates the inputs and forwards the request.
2. FastAPI pins a published build, checks its known coverage, and resolves the two revision IDs using temporal SQL.
3. For **Compare**, it reads the normalized sections and produces a deterministic paragraph/word diff.
4. For **Ask**, it embeds the question and ranks eligible passages separately for the earlier and later revisions.
5. If generation is enabled, the model receives labelled evidence (`A1…` and `B1…`) and returns structured statements
   with citation IDs. The server validates the response and attaches stored excerpts and permanent revision links.
6. MediaWiki renders the result. Unknown coverage, unavailable text, insufficient evidence and model failures have
   explicit states rather than silently substituting a different revision.

## Technical concepts

### Revisions, snapshots, and coverage

A **revision** is a saved article version with an ID and edit timestamp. A **snapshot at time T** is the
latest applicable revision at or before T, within a history window that this project has verified.
An `oldid` link identifies the saved revision; the requested time and the revision's save time can differ.

Coverage is essential. Downloading a few revisions does not prove which one was current between them.
For each captured window, the importer follows API continuation and records the predecessor before the
window starts. Intervals use **[start, end)**: the start belongs to the interval, the end does not.
At the exact next edit, the next revision takes over. UTC and a deterministic revision-ID tie-break
handle timestamp boundaries consistently. Outside verified windows, the service returns `not_covered`.

### System-versioned tables: asking a database about the past

MariaDB system-versioned tables expose row history through `FOR SYSTEM_TIME AS OF`. Ordinarily their
system period describes changes made to the database. Here the importer deliberately **backfills that
period with Wikipedia's edit timeline**, so `AS OF '2021-12-15 …'` means the source article's state then.
See MariaDB's [system-versioned tables reference](https://mariadb.com/docs/server/reference/sql-structure/temporal-tables/system-versioned-tables).

This distinction matters: importing an old revision today must not make it appear to have originated
today. The archive retains `ingested_at` separately, and build metadata records local publication.
The backfilled system period is a source-history projection; it is not an audit of when this service
first learned each fact. Explicit historical insertion is enabled only for importer sessions, and
every projected interval is closed at the verified coverage end.

### Bitemporal data: two independent dates in one question

**Application time** expresses when a claim says a tenure applied. **System time in this projection**
expresses when Wikipedia asserted that claim. Combining these periods makes the claims table
bitemporal. MariaDB supports both periods in the same table; the project uses `AS OF` for the source
view and date predicates for the tenure. See [bitemporal tables](https://mariadb.com/docs/server/reference/sql-structure/temporal-tables/bitemporal-tables).

| Date | Example | Meaning |
|---|---|---|
| Source-view time, T | Wikipedia as of June 15 or June 25, 2023 | Which revision's assertions are eligible? |
| Claimed valid date, D | June 22, 2023 | Which of those assertions covers the requested tenure date? |
| Local ingestion time | When this demo downloaded the revision | Operational provenance, stored separately |

The query is therefore **“According to Wikipedia at T, who did it say held the office on D?”**.
A missing tenure claim means that the supported extraction from that revision did not supply one;
it does not prove nobody held the office. Date precision and unknown/ongoing ends are stored explicitly.

### Embeddings and cosine distance

An **embedding** is a model-produced numeric representation of text. MiniLM maps a question and each
passage into 384 numbers. Mean pooling combines token representations, and L2 normalization gives
each vector unit length. MariaDB stores those vectors as `VECTOR(384)` and ranks passages using
`VEC_DISTANCE_COSINE`: smaller distances indicate closer vector directions. Similarity is a relevance
signal, not a factual confidence score. See the [MariaDB vector reference](https://mariadb.com/docs/server/reference/sql-structure/vectors/vector-overview).

The project has two retrieval paths with different purposes:

| Path | Eligible evidence | Search strategy | Used for historical answers? |
|---|---|---|---|
| Revision evidence | Passages joined to one exact resolved revision, parser version and embedding model | Exact cosine ranking over that eligible set | Yes |
| Corpus topic search | Imported passages from the organizer S dataset | Approximate nearest-neighbour search using the native HNSW vector index | No |

HNSW (Hierarchical Navigable Small World) searches a graph of vectors to find nearby candidates
without comparing every vector. Its speed comes with a recall tradeoff: it can miss neighbours that
an exhaustive search would find. That is why the benchmarks report recall against exact results.
MariaDB uses a modified HNSW implementation for its vector index.
[Implementation reference](https://mariadb.org/projects/mariadb-vector/).

For historical answers, filtering **before** ranking is the key rule. Searching all revisions and then
discarding future passages can leave too few results or miss the best eligible passages. The measured
query plan uses a revision membership join and exact ranking. Merely declaring a vector index does
not mean every similarity query uses it.

### Retrieval-augmented generation and provenance

**RAG** means retrieving source material before asking a language model to compose an answer.
Here retrieval is also constrained by time. Each answer side has its own evidence IDs, revision ID,
and stored excerpt. The server rejects malformed structures, unknown or wrong-side citation IDs, and
uncited statements; it constructs source URLs itself rather than trusting model-generated links.

These checks establish citation provenance and structural validity. They do **not** prove that every
generated sentence follows logically from its citation, or erase knowledge already inside the model.
Readers can inspect the excerpts, diff and original revisions. Claim extraction adds substring,
holder, office and date checks, but still inherits source errors and extraction limitations.

### Reproducible builds and atomic publication

Raw wikitext is archived separately from derived text. Normalized passages are deduplicated by text
hash, parser version and embedding model, so unchanged text can share an embedding across revisions.
The membership table still records which revisions contain it; sharing storage never makes it
eligible for a revision that did not contain it.

A build is a versioned set of coverage and snapshot intervals. It is constructed and validated before
the active-build pointer changes in a transaction. Query code pins the selected build so a comparison
uses a consistent snapshot projection while another build is prepared. Claims have their own build
linked to that snapshot build. Unchanged inputs reuse published results. This keeps partial imports
and failed validation from becoming the public history.

## Pinned versions

| component | version |
|---|---|
| MediaWiki core | git tag **1.43.11** (LTS) from `gerrit.wikimedia.org/r/mediawiki/core.git` (commit `0e3538fb0e48`) |
| Vector skin | `REL1_43` (commit `f885ee1914ee`) |
| PHP runtime | `mediawiki:1.43.11`; local Compose bind-mounts the pinned core checkout, prepared with Composer `2.8.12`. Fly uses core, dependencies and bundled Vector from the official image |
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
The committed API fixtures provide **five English articles**. The six-article Fly demo additionally
imports Erik Edner from organizer M. To reproduce that locally:

```bash
mkdir -p downloads/m-2026-10
curl -fL https://github.com/MariaDB/mariadb-wikipedia-datasets/releases/download/m-2026-10/versions-01.parquet -o downloads/m-2026-10/versions-01.parquet
curl -fL https://github.com/MariaDB/mariadb-wikipedia-datasets/releases/download/m-2026-10/SHA256SUMS -o downloads/m-2026-10/SHA256SUMS
docker compose exec -T service python -m wtm.cli import-parquet --file /downloads/m-2026-10/versions-01.parquet --sha256sums /downloads/m-2026-10/SHA256SUMS --page-id 186
docker compose exec -T service python -m wtm.cli build
docker compose exec -T service python -m wtm.cli claims --no-llm --import-cache /app/fixtures/claims/nemotron-extractions.jsonl.gz
```

The importer checks the Parquet checksum and page history. Organizer S is a separate current-text
corpus used for ANN benchmarks (`make corpus`); it is not the source of historical article snapshots.

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

### Using the live demo

Open the [public special page](https://wiki-time-machine-joeng03.fly.dev/index.php/Special:WikiTimeMachine)
and select an example button. **Compare versions** resolves the revisions and shows the diff; **Ask**
uses the question field to retrieve evidence and request a generated answer. For the prime minister
and president examples, **Held office on** supplies the independent application-time date.

The public demo is a prepared archive, not an unrestricted live Wikipedia search. The five English
articles cover selected windows; Erik Edner has the imported organizer history. Choosing a date
outside those windows gives an explicit coverage result. No visitor account is needed to read it;
editing and public account creation are disabled.

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

## Data model and API

The full schema is in [wtm_schema.sql](extensions/WikiTimeMachine/sql/wtm_schema.sql).

| Tables | Purpose |
|---|---|
| `wiki_page`, `wiki_revision`, `wiki_fetch_window` | Canonical page identity, archived source revisions and evidence of complete capture windows |
| `wiki_build`, `wiki_active_build`, `wiki_coverage` | Build provenance, the active publication pointer and verified coverage |
| `wiki_snapshot` | System-versioned mapping from source time to revision |
| `wiki_revision_text`, `wiki_chunk`, `wiki_revision_chunk` | Derived text, shared passage vectors and exact revision membership |
| `corpus_passage` | Separate organizer S passages for corpus-wide ANN search |
| `wiki_claim`, `wiki_revision_claim`, `wiki_claim_extraction` | Structured tenure claims, revision-specific evidence and cached extraction results |
| `wiki_claim_build`, `wiki_claim_snapshot` | Published claim builds and bitemporal claim intervals |

MediaWiki exposes the visitor-facing actions below through `api.php`. PHP translates their parameters
to internal FastAPI requests; the two API interfaces deliberately have different field names.

| MediaWiki action | Internal FastAPI route | Purpose |
|---|---|---|
| `wtmarticles` | `GET /articles` | List prepared articles and coverage windows |
| `wtmsnapshot` | `GET /snapshot` | Resolve one article at one time |
| `wtmcompare` | `GET /compare` | Resolve two snapshots and return the diff |
| `wtmask` | `POST /ask` | Retrieve revision-specific evidence and optionally generate an answer |
| `wtmclaims` | `GET /claims` | Compare tenure claims at a valid date under two source views |
| Internal only | `GET /timeline`, `GET /search`, `GET /health` | Inspect temporal intervals, search the separate corpus, check service/database state |

For example, this public read request resolves the Olkiluoto snapshots without calling the LLM:

```bash
curl -fsSG 'https://wiki-time-machine-joeng03.fly.dev/api.php' \
  --data-urlencode 'action=wtmcompare' \
  --data-urlencode 'format=json' \
  --data-urlencode 'formatversion=2' \
  --data-urlencode 'page=1977274' \
  --data-urlencode 'before=2021-12-15' \
  --data-urlencode 'after=2023-04-20'
```

MediaWiki uses `wiki`, `page` and `validat`; the corresponding FastAPI fields are `wiki_id`, `page_id`
and `valid_at`. Model-backed questions are limited to 500 characters by the current service.

## Deployment on Fly.io

The full stack was deployed on **14 November 2026** under the personal Fly.io organization. Fly runs
each Docker image as its own Machine; it does not run this repository's Compose file directly.
Compose remains the local development entry point. The cloud deployment uses three Fly apps in
Stockholm (`arn`) and the same pinned MediaWiki, MariaDB and Python runtime versions.

### Do these services need separate machines?

**No.** MariaDB must run somewhere, but it does not require a dedicated machine. MediaWiki (the
PHP application serving the interface), FastAPI and MariaDB can all run as separate containers on
one server. Our local Docker Compose setup already does that. Containers are separate processes
and filesystems; they are not necessarily separate servers.

The three-machine arrangement below describes the **current deployment**, not an architectural
requirement. It gives each service its own resources and update lifecycle. For this small demo,
one adequately sized machine is a reasonable simpler target; all services would share its CPU,
memory and failure/restart boundary. Persistent database storage is still required in either layout.

Fly also supports [multiple containers on one Machine, including a Compose deployment path](https://fly.io/docs/machines/guides-examples/multi-container-machines/).
Moving this demo there requires a production Compose/config adaptation: prebuilt images where
needed, local service connections, and explicit Fly volume mounts. Fly's documented Compose path
does not preserve Compose `volumes:` declarations automatically. Our local checkout/config bind
mounts cannot be treated as cloud persistent storage. The live three-machine deployment has not
been migrated by this documentation change.

```text
Public browser
    │ HTTPS
    ▼
wiki-time-machine-joeng03              MediaWiki :80
    │ private IPv6 HTTP
    ▼
wiki-time-machine-joeng03-api          FastAPI :8000 ──HTTPS──▶ optional Nemotron endpoint
    │ private IPv6 SQL
    ▼
wiki-time-machine-joeng03-db           MariaDB :3306

MediaWiki also connects directly to MariaDB for its own schema.
```

| Config / image build | Machine size | Persistent volume | Public access |
|---|---|---|---|
| [fly.toml](fly.toml) / [mediawiki.Dockerfile](docker/fly/mediawiki.Dockerfile) | 1 shared CPU, 1 GB RAM | `wikiconfig`, 1 GB, mounted at `/data` | HTTPS at `wiki-time-machine-joeng03.fly.dev` |
| [fly.service.toml](fly.service.toml) / [service.Dockerfile](docker/fly/service.Dockerfile) | 2 shared CPUs, 2 GB RAM | `hfcache`, 1 GB, mounted at `/cache` | Private network only |
| [fly.db.toml](fly.db.toml) / [db.Dockerfile](docker/fly/db.Dockerfile) | 1 shared CPU, 2 GB RAM | `dbdata`, 3 GB, mounted at `/var/lib/mysql` | Private network only |

The API and database have no public IP allocations. Their `.internal` hostnames resolve through Fly's
private network; both images listen on IPv6 for those connections. The public site forces HTTPS, and
its health check calls MediaWiki's site-info API. This checks the wiki's availability; the separate
FastAPI `/health` response and end-to-end tests check the retrieval stack.

### Images, secrets and persistent state

The MediaWiki image copies the extension into the official `mediawiki:1.43.11` image. It uses the
core, dependencies and bundled skin supplied there, rather than copying the ignored local core
checkout. At first boot, the entrypoint installs MediaWiki and stores generated settings and keys
on `/data/LocalSettings.php`. The runtime override reads the public URL, database password and
internal API URL from environment variables and disables detailed public exception output.

The FastAPI image contains the service, SQL and committed fixtures. MiniLM is downloaded when first
needed and retained on `/cache`; subsequent requests reuse the loaded model in the running process.
The MariaDB image includes the database/user initialization script and the UTC, historical-insertion
and vector-cache configuration. Database files survive machine replacement on the `dbdata` volume.

| Fly app | Required secrets | Additional configuration |
|---|---|---|
| Database | `MARIADB_ROOT_PASSWORD`, `MW_DB_PASSWORD`, `WTM_IMPORTER_PASSWORD`, `WTM_READER_PASSWORD` | Initialization creates separate MediaWiki and application schemas/users |
| API | `WTM_READER_PASSWORD`, `WTM_IMPORTER_PASSWORD` | Optional `WTM_LLM_*` and `WTM_USER_AGENT`; database host/model choice are in its Fly config |
| MediaWiki | `MW_DB_PASSWORD`, `MW_ADMIN_PASSWORD` | Public server URL, database host and internal service URL are in its Fly config |

Passwords were generated for the cloud deployment, stored as Fly secrets and kept in the ignored
local recovery file `data/fly-secrets.json` (restricted to its owner). The existing optional LLM
configuration was copied from the local Compose environment into Fly secrets. No credentials belong
in Git or Docker build arguments; `.dockerignore` excludes local secrets and state from image builds.
Rotating database credentials also requires changing the actual MariaDB user passwords: Fly secret
updates alone do not rerun initialization on an existing database volume.

### Provisioning and initialization

For a new account/deployment, install the official Fly CLI and sign in with `fly auth login`. Choose
three available app names, update all three configs and their cross-references, and create each app
with `fly apps create <app-name> --org <organization>`. The checked-in names belong to this deployment.
Do not recreate its existing apps or volumes for a routine update.

The original volume provisioning commands were:

```bash
fly volumes create dbdata -a wiki-time-machine-joeng03-db -r arn --size 3 -y
fly volumes create hfcache -a wiki-time-machine-joeng03-api -r arn --size 1 -y
fly volumes create wikiconfig -a wiki-time-machine-joeng03 -r arn --size 1 -y
```

Set the secrets listed above for each app before its first deployment, using `fly secrets import
--stage -a <app-name>` with `NAME=VALUE` input from a private file or secret manager. Deploy the database
first, then the API. Once the database is ready, initialize the archive:

```bash
fly deploy -c fly.db.toml --remote-only --ha=false
fly deploy -c fly.service.toml --remote-only --ha=false
fly ssh console -a wiki-time-machine-joeng03-api -C "python -m wtm.cli doctor --check-insert-history"
fly ssh console -a wiki-time-machine-joeng03-api -C "python -m wtm.cli migrate"
fly ssh console -a wiki-time-machine-joeng03-api -C "python -m wtm.cli load /app/fixtures/real/*.jsonl.gz"
fly ssh console -a wiki-time-machine-joeng03-api -C "python -m wtm.cli build"
fly ssh console -a wiki-time-machine-joeng03-api -C "python -m wtm.cli claims --no-llm --import-cache /app/fixtures/claims/nemotron-extractions.jsonl.gz"
fly deploy -c fly.toml --remote-only --ha=false
```

The CLI expands the capture glob itself. For the sixth article, download organizer M using the local
instructions above, then transfer and import the file:

```bash
fly ssh sftp put -a wiki-time-machine-joeng03-api downloads/m-2026-10/versions-01.parquet /cache/versions-01.parquet
fly ssh sftp put -a wiki-time-machine-joeng03-api downloads/m-2026-10/SHA256SUMS /cache/SHA256SUMS
fly ssh console -a wiki-time-machine-joeng03-api -C "python -m wtm.cli import-parquet --file /cache/versions-01.parquet --sha256sums /cache/SHA256SUMS --page-id 186"
fly ssh console -a wiki-time-machine-joeng03-api -C "python -m wtm.cli build"
fly ssh console -a wiki-time-machine-joeng03-api -C "python -m wtm.cli claims --no-llm --import-cache /app/fixtures/claims/nemotron-extractions.jsonl.gz"
```

The public deployment contains these six article histories and cached claims. The separate organizer
S corpus used in the ANN benchmarks was **not** imported into Fly; `/search` there is not a populated
corpus-search demo. Ingestion is manual, so the archive does not automatically track new Wikipedia edits.

### Updates, monitoring and verification

For code updates, run `fly deploy` with the affected config from the repository root. PHP/UI changes
need `fly.toml`; Python changes need `fly.service.toml`. Database-image changes use `fly.db.toml` and
deserve separate backup planning. Rebuilding an image does not automatically reload fixtures or rebuild
the archive. Schema changes require an explicit migration; new data needs validation and publication.

```bash
fly status -c fly.toml
fly status -c fly.service.toml
fly status -c fly.db.toml
fly logs -c fly.toml
fly logs -c fly.service.toml
fly logs -c fly.db.toml
```

To repeat the deployed end-to-end tests without including test files in the runtime image:

```bash
fly ssh sftp put -a wiki-time-machine-joeng03-api extensions/WikiTimeMachine/service/tests_e2e/test_mediawiki_e2e.py /tmp/test_mediawiki_e2e.py
fly ssh console -a wiki-time-machine-joeng03-api -C "env PYTHONPATH=/app WTM_MW_URL=https://wiki-time-machine-joeng03.fly.dev pytest -q /tmp/test_mediawiki_e2e.py"
```

At deployment, all **5 tests passed** against the public HTTPS site and cloud database. A separate
public `wtmask` request returned generation status `ok` with Nemotron statements and revision-specific
citations. The browser comparison displayed both exact revision links and the expected paragraph diff.
The published cloud build has **286 snapshot intervals / 1,703 checks** and **21 claim rows / 106 checks**.

### Hosting limits and costs

This is one machine per app with persistent volumes and scheduled snapshots. It has no database
replica or automatic failover; maintenance and host failures can interrupt the demo. Keep the database
and API running, since direct `.internal` connections do not wake stopped machines. Do not scale the
database to multiple machines without a replication design.

Fly charges for machines and storage, and model requests use the configured external endpoint.
The demo does not yet have application-level per-visitor model quotas. Before wider public use, add
appropriate abuse controls, independent database backups, monitoring and an availability plan.
Snapshots alone are not an independent backup strategy. See the
[Fly volume documentation](https://fly.io/docs/volumes/overview/) for persistence and failure behaviour.
The smaller [deployment runbook](docker/fly/README.md) is available for routine operations.

## Measured results

See `extensions/WikiTimeMachine/docs/benchmarks.md` (row counts, EXPLAIN plans, latency; no LLM calls).
Summary (SQL only, p50 / p95):

These are local benchmark measurements, not Fly.io latency measurements or a large-scale load test.
`p50` is the median, `p95` is the 95th percentile, and recall@10 measures overlap with the exact top-10
neighbours for the benchmark queries. Recall is retrieval agreement, not answer accuracy.

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
| Offline reproduction from committed English fixtures in a fresh database | `make demo` | 258 intervals / 1,536 checks; 21 claim rows / 106 checks. Add organizer M for 286 / 1,703 |
| Deployed Fly.io end-to-end suite | Commands in the deployment section | **5 passed** against public HTTPS and deployed DB |
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

* **Evidence is not ground truth:** the app reconstructs source assertions, including mistakes and outdated claims.
  Citation validation verifies provenance and response structure, not logical entailment of every generated sentence.
* **Bounded archive:** only imported articles/windows are available. This is not a complete Wikipedia mirror or a live
  synchronization service, and the deployed Fly demo does not contain the organizer S ANN corpus.
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

## Contributing to MediaWiki and Wikimedia

WikiTimeMachine uses MediaWiki's extension mechanism and lives in a separate repository. That is the
appropriate current boundary for its special page, API modules and external service; developing it does
not require merging this whole repository into MediaWiki core.

Upstream acceptance remains future work. The proposed path is developer feedback, an independently
installable package, compatibility/coding/localisation checks, extension documentation, an appropriate
repository-hosting request, and review through Gerrit. A GitHub fork of the core mirror is not that
review path. See [the contribution plan](CONTRIBUTING.md) and
[MediaWiki's Gerrit workflow](https://www.mediawiki.org/wiki/Gerrit/How_Gerrit_works).

The Fly demo runs our own MediaWiki installation. It is not deployed on Wikipedia. Wikimedia hosting
or a merged extension change would still be separate from approval to run on Wikimedia wikis, which
requires the applicable community, security, performance and operations reviews. The external Python
service and model dependency need an agreed operational plan. See
[writing an extension for deployment](https://www.mediawiki.org/wiki/Writing_an_extension_for_deployment).

## Layout

```
docker-compose.yml, Makefile, .env.example, scripts/setup-mediawiki.sh
docker/mariadb/{init,conf}            DB users/schemas, UTC + secure_timestamp=NO
docker/mediawiki/LocalSettings.wtm.php
docker/fly/                          cloud Dockerfiles, first-boot entrypoint, runtime overrides, runbook
fly.toml, fly.service.toml, fly.db.toml
CONTRIBUTING.md                      proposed upstream contribution path and current status
mediawiki/                            pinned upstream checkout (gitignored, recreated by setup)
extensions/WikiTimeMachine/
  extension.json, WikiTimeMachine.alias.php, i18n/, includes/ (SpecialPage, API modules, ServiceClient),
  modules/ (ResourceLoader JS/LESS), tests/phpunit/
  sql/wtm_schema.sql                  app schema (SVT + VECTOR)
  service/                            Python service + CLI (wtm/), tests/, tests_e2e/, Dockerfile
  fixtures/real (Wikipedia, CC BY-SA), fixtures/claims (Nemotron extraction cache), fixtures/synthetic (CI), docs/
```

Wikipedia text © Wikipedia contributors, CC BY-SA 4.0. Code: GPL-2.0-or-later (MediaWiki extension convention).
