# Fly.io demo deployment

The three Fly apps reuse the pinned Docker runtimes. Only MediaWiki has a public
HTTP service. FastAPI and MariaDB communicate over Fly's private IPv6 network.
Each app runs one machine in Stockholm (`arn`); this is a demo deployment with
downtime during updates, not a replicated production service.

| Config | App | Persistent volume |
| --- | --- | --- |
| `fly.toml` | `wiki-time-machine-joeng03` | `wikiconfig`: generated wiki settings and keys |
| `fly.service.toml` | `wiki-time-machine-joeng03-api` | `hfcache`: MiniLM download cache |
| `fly.db.toml` | `wiki-time-machine-joeng03-db` | `dbdata`: MariaDB data |

Secrets are stored with Fly secrets, never in the images. The initial deployment's
generated credentials have a local recovery copy in ignored `data/fly-secrets.json`.
Do not commit or share that file. The service also uses the existing optional LLM
configuration from the local Compose environment.

## Update an existing deployment

From the repository root, deploy only the apps affected by your change:

```sh
fly deploy -c fly.db.toml --remote-only --ha=false
fly deploy -c fly.service.toml --remote-only --ha=false
fly deploy -c fly.toml --remote-only --ha=false
```

Keep the database and API running; private DNS does not start stopped machines.
Do not scale the database to multiple machines without configuring replication.

## Initialize the fixture demo

Once the database and API are running:

```sh
fly ssh console -a wiki-time-machine-joeng03-api -C "python -m wtm.cli doctor --check-insert-history"
fly ssh console -a wiki-time-machine-joeng03-api -C "python -m wtm.cli migrate"
fly ssh console -a wiki-time-machine-joeng03-api -C "python -m wtm.cli load /app/fixtures/real/*.jsonl.gz"
fly ssh console -a wiki-time-machine-joeng03-api -C "python -m wtm.cli build"
fly ssh console -a wiki-time-machine-joeng03-api -C "python -m wtm.cli claims --no-llm --import-cache /app/fixtures/claims/nemotron-extractions.jsonl.gz"
```

The loader accepts the glob itself. Import/build operations are idempotent for
unchanged inputs. Corpus-wide ANN search needs its separate organizer data import;
the fixture initialization provides article comparisons and bitemporal claims.

The deployed demo also includes Erik Edner from the organizer M dataset. To
reproduce it, download and verify `versions-01.parquet` and `SHA256SUMS` as
described in the root README, then:

```sh
fly ssh sftp put -a wiki-time-machine-joeng03-api downloads/m-2026-10/versions-01.parquet /cache/versions-01.parquet
fly ssh sftp put -a wiki-time-machine-joeng03-api downloads/m-2026-10/SHA256SUMS /cache/SHA256SUMS
fly ssh console -a wiki-time-machine-joeng03-api -C "python -m wtm.cli import-parquet --file /cache/versions-01.parquet --sha256sums /cache/SHA256SUMS --page-id 186"
```

Run `build` and `claims` again after that import to publish the expanded demo.

The first MediaWiki boot installs its schema and creates the administrator account.
Generated settings remain on its volume. Runtime environment values override the
database password and public URL, so they can be changed without rebuilding keys.

## Operations

```sh
fly status -c fly.toml
fly logs -c fly.toml
fly logs -c fly.service.toml
fly logs -c fly.db.toml
```

Fly charges for the machines and volumes. Volumes have scheduled snapshots, but
the single database has no replica. Keep separate database backups before storing
data that cannot be rebuilt from the committed fixtures.

## Verified deployment (14 November 2026)

- Public special page renders; browser comparison displays both exact revision
  links and the paragraph diff.
- Six articles, 286 snapshot intervals; 1,703 snapshot checks passed.
- 21 bitemporal claim rows; 106 claim checks passed.
- All five existing end-to-end tests passed against the public HTTPS site and
  deployed database. The test file was uploaded temporarily to `/tmp` on the API
  machine and run with `PYTHONPATH=/app` and `WTM_MW_URL` set to the public URL.
- A separate public `wtmask` request returned generation status `ok` with
  Nemotron statements and revision-specific citations.
- API and database apps have no public IP allocations.
