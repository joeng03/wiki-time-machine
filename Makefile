# Wikipedia Time Machine -- tested commands (see README.md)
DC := docker compose
SVC := $(DC) exec -T service

.PHONY: setup up down migrate demo fetch-demo test test-db test-e2e test-php bench corpus doctor verify-links

setup:            ## clone pinned MediaWiki 1.43.11 + Vector, composer install, start stack, install wiki
	./scripts/setup-mediawiki.sh
	$(MAKE) migrate

up:
	$(DC) up -d --build

down:
	$(DC) down

doctor:           ## verify MariaDB version, VECTOR and historical-insert support
	$(SVC) python -m wtm.cli doctor --check-insert-history

migrate:          ## apply the app schema (separate from the MediaWiki schema)
	$(SVC) python -m wtm.cli migrate
	$(SVC) python -m wtm.cli migrate --database wtm_test

demo:             ## offline: load the shipped real-data fixtures, build + validate + publish the SVT projection
	$(SVC) python -m wtm.cli load '/app/fixtures/real/*.jsonl.gz'
	$(SVC) python -m wtm.cli build
	@echo "Open http://localhost:8080/index.php/Special:WikiTimeMachine"

fetch-demo:       ## live re-capture of the demo fixtures (needs WTM_USER_AGENT in .env)
	$(SVC) python -m wtm.cli fetch --title "Olkiluoto Nuclear Power Plant" --rvlimit 10 \
	  --window 2021-12-01T00:00:00Z/2022-01-01T00:00:00Z --window 2022-03-01T00:00:00Z/2022-04-01T00:00:00Z \
	  --window 2023-04-08T00:00:00Z/2023-04-22T00:00:00Z --out /app/fixtures/real/olkiluoto.jsonl.gz
	$(SVC) python -m wtm.cli fetch --title "Finland–NATO relations" --rvlimit 10 \
	  --window 2022-05-17T00:00:00Z/2022-05-19T00:00:00Z --window 2023-04-04T12:00:00Z/2023-04-04T14:00:00Z \
	  --out /app/fixtures/real/finland-nato.jsonl.gz
	$(SVC) python -m wtm.cli fetch --title "Helsinki Central Library Oodi" --rvlimit 10 \
	  --window 2018-11-01T00:00:00Z/2019-01-20T00:00:00Z --out /app/fixtures/real/oodi.jsonl.gz

test: test-db test-php   ## unit + DB integration tests (synthetic fixtures, hash embedder; no network)

test-db:
	$(DC) run --rm -T service pytest -q tests

test-e2e:         ## through MediaWiki's API, on the real demo data (run `make demo` first)
	$(SVC) pytest -q tests_e2e

test-php:
	$(DC) exec -T mediawiki php vendor/bin/phpunit extensions/WikiTimeMachine/tests/phpunit/unit

corpus:           ## organizer S dataset (English articles) -> corpus_passage for the ANN demo
	mkdir -p downloads/s-2026-10 && cd downloads/s-2026-10 && \
	  curl -sSLO https://github.com/MariaDB/mariadb-wikipedia-datasets/releases/download/s-2026-10/articles-en.parquet && \
	  curl -sSLO https://github.com/MariaDB/mariadb-wikipedia-datasets/releases/download/s-2026-10/SHA256SUMS && \
	  grep articles-en.parquet SHA256SUMS | shasum -a 256 -c -
	$(SVC) python -m wtm.cli corpus --file /downloads/s-2026-10/articles-en.parquet $(if $(LIMIT),--limit $(LIMIT),)

bench:            ## row counts, plans, latency (no LLM)
	$(SVC) python -m wtm.cli bench --label "$(or $(LABEL),local)" --out /app/fixtures/bench-$(or $(LABEL),local).md  # copy to docs/bench/ to keep

verify-links:     ## live check that oldid links serve the stored text (needs WTM_USER_AGENT)
	$(SVC) python -m wtm.cli verify-links --limit 400
