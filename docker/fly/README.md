# Fly.io demo deployment

The public demo uses **one Machine** in Stockholm (`arn`): **2 shared CPUs, 4 GB RAM,
10 GB persistent storage**. Three containers run MediaWiki, FastAPI and MariaDB.
Only MediaWiki's port 80 is exposed through Fly Proxy with HTTPS.

See the root [README](../../README.md#deployment-on-flyio) for the concepts, schema,
full provisioning instructions, migration details and limitations.

## Files and storage

- `fly.toml`: public app, resources, environment, `/data` mount and health check.
- `docker/fly/machine.json`: container images, local networking and startup order.
  Keep `mediawiki` first: the Fly deploy workflow replaces the primary image.
- `fly.api-image.toml` and `fly.db-image.toml`: build/push images only; do not deploy
  these as separate applications.
- One volume, historically named `wikiconfig`, stores `/data/LocalSettings.php`,
  `/data/mysql` and `/data/hf`. Do not delete it during deployment.
- Secrets belong to `wiki-time-machine-joeng03`. Private recovery credentials and
  migration backups live in the ignored local `data/` directory, never in Git.

## Update and inspect

From the repository root:

```sh
fly deploy -c fly.toml --remote-only --ha=false
fly status -a wiki-time-machine-joeng03
fly machine list -a wiki-time-machine-joeng03
fly logs -a wiki-time-machine-joeng03
fly ssh console -a wiki-time-machine-joeng03 --container api -C 'df -h /data'
```

For a Python change, build/push a uniquely labelled image:

```sh
fly deploy -c fly.api-image.toml --remote-only --build-only --push --image-label api-NEW_VERSION
```

Update the `api` image in `machine.json` to that image's immutable digest and deploy
`fly.toml`. Database images use `fly.db-image.toml` with the same workflow. Back up
before a database upgrade. Deployments do not automatically migrate or rebuild data.

All containers share loopback networking. MediaWiki uses `127.0.0.1:8000` for the
API and `127.0.0.1:3306` for its database. The API uses that same local database.
MariaDB and the API listen only on loopback. Startup dependencies wait for MariaDB's
TCP readiness before starting the API, then start MediaWiki after the API process.

## Import data and test

On a fresh database, use the initialization commands in the root README. Target
`--container api` for `python -m wtm.cli` commands. The committed fixtures provide
five English articles; organizer M adds the sixth, Erik Edner. The organizer S
corpus used in local ANN benchmarks is not populated in the public demo.

Run the public end-to-end suite:

```sh
fly ssh sftp put -a wiki-time-machine-joeng03 --container api extensions/WikiTimeMachine/service/tests_e2e/test_mediawiki_e2e.py /tmp/test_mediawiki_e2e.py
fly ssh console -a wiki-time-machine-joeng03 --container api -C "env PYTHONPATH=/app WTM_MW_URL=https://wiki-time-machine-joeng03.fly.dev pytest -q /tmp/test_mediawiki_e2e.py"
```

Also check generation status `ok` on a model-backed question: the suite accepts
useful evidence fallback when the external model is unavailable.

## Migration and recovery

The original deployment used three Machines. Consolidation retains the public
app and wiki settings, extends its volume to 10 GB, and restores both schemas into
the local MariaDB container. The backup uses `mariadb-dump --dump-history` to preserve
system-versioned rows. Never substitute a current-row-only dump.

Private local backups are under `data/fly-migration/`, with credentials under
`data/fly-secrets.json`. Old API/database machines are retired after verification.
Retained old volumes provide a rollback copy and continue to incur storage charges.
Scheduled snapshots are enabled, but keep independent backups for irreplaceable data.

The deployment is a demo: one failure/restart boundary, no replica or automatic
failover, and no application-level per-visitor model quotas. Do not add independent
machines with `fly scale count` without first designing database replication.

## Verified after consolidation (14 November 2026)

- Exactly one Machine remains across the original three apps; the old API and
  database Machines were removed. Their 4 GB of old volumes remain as rollback
  storage (about US$0.02/day at the published volume rate).
- The Machine has 2 shared CPUs, 4 GB RAM and the original volume extended to 10 GB.
- Both schemas were restored with history: six pages, 286 published snapshot
  intervals and 21 published claim rows. Wiki settings match the original backup.
- A normal redeployment/restart preserves the database, wiki keys and model cache.
- All five existing end-to-end tests pass against the public site after migration.
- A separate model-backed request returns generation status `ok` with the expected
  historical revision IDs. Database capability checks confirm historical inserts
  and native vector support.
