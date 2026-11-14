-- WikiTimeMachine application schema (separate from the MediaWiki schema).
-- Target: MariaDB 11.8 LTS (verified on mariadb:11.8.9).
-- Applied by: wtm migrate   (importer credentials)
-- All DATETIME/TIMESTAMP values are UTC; every session sets time_zone = '+00:00'.

-- Canonical pages (redirects resolved to page id before fetch).
CREATE TABLE IF NOT EXISTS wiki_page (
    wiki_id      VARCHAR(32)     NOT NULL,
    page_id      BIGINT UNSIGNED NOT NULL,
    title        VARCHAR(512)    NOT NULL,
    source_host  VARCHAR(128)    NOT NULL,
    namespace    INT             NOT NULL DEFAULT 0,
    updated_at   DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (wiki_id, page_id),
    KEY idx_title (title(191))
) ENGINE=InnoDB;

-- Archive of every fetched revision (staging + permanent source of truth).
-- Idempotent on (wiki_id, revision_id). Raw wikitext preserved unchanged; no editor identities.
CREATE TABLE IF NOT EXISTS wiki_revision (
    wiki_id            VARCHAR(32)     NOT NULL,
    revision_id        BIGINT UNSIGNED NOT NULL,
    page_id            BIGINT UNSIGNED NOT NULL,
    parent_id          BIGINT UNSIGNED NULL,
    revision_ts        DATETIME(6)     NOT NULL COMMENT 'upstream edit time, UTC',
    content_model      VARCHAR(64)     NULL,
    content_available  BOOLEAN         NOT NULL,
    wikitext           LONGTEXT COMPRESSED NULL,
    text_sha256        CHAR(64)        NULL,
    source             VARCHAR(64)     NOT NULL COMMENT 'api | parquet | synthetic',
    ingested_at        DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'local operational metadata',
    PRIMARY KEY (wiki_id, revision_id),
    KEY idx_page_time (wiki_id, page_id, revision_ts, revision_id)
) ENGINE=InnoDB;

-- One row per fetch attempt of a coverage window [coverage_start, coverage_end).
-- Only status='complete' windows are ever published.
CREATE TABLE IF NOT EXISTS wiki_fetch_window (
    window_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    wiki_id          VARCHAR(32)     NOT NULL,
    page_id          BIGINT UNSIGNED NOT NULL,
    coverage_start   DATETIME(6)     NOT NULL,
    coverage_end     DATETIME(6)     NOT NULL,
    status           ENUM('fetching','complete','failed') NOT NULL,
    source           VARCHAR(64)     NOT NULL,
    predecessor_ids  JSON            NULL COMMENT 'revisions strictly before coverage_start; [] = page did not exist yet',
    revision_count   INT UNSIGNED    NOT NULL DEFAULT 0,
    api_pages        INT UNSIGNED    NOT NULL DEFAULT 0,
    manifest         JSON            NULL,
    window_key       CHAR(64)        NOT NULL COMMENT 'sha256 of wiki/page/start/end/source',
    created_at       DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (window_id),
    UNIQUE KEY uq_window (window_key),
    KEY idx_page (wiki_id, page_id, status),
    CHECK (coverage_start < coverage_end)
) ENGINE=InnoDB;

-- Builds of the published projection. Exactly one is active.
CREATE TABLE IF NOT EXISTS wiki_build (
    build_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    status            ENUM('building','validated','published','failed','superseded') NOT NULL,
    input_fingerprint CHAR(64)        NOT NULL,
    parser_version    VARCHAR(64)     NOT NULL,
    embed_model       VARCHAR(128)    NOT NULL,
    snapshot_rows     INT UNSIGNED    NOT NULL DEFAULT 0,
    validation        JSON            NULL,
    created_at        DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    published_at      DATETIME(6)     NULL,
    PRIMARY KEY (build_id),
    KEY idx_fp (input_fingerprint, status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS wiki_active_build (
    id        TINYINT UNSIGNED NOT NULL PRIMARY KEY,
    build_id  BIGINT UNSIGNED  NOT NULL,
    CHECK (id = 1)
) ENGINE=InnoDB;

-- Merged, verified coverage per build/page: [coverage_start, coverage_end).
CREATE TABLE IF NOT EXISTS wiki_coverage (
    build_id        BIGINT UNSIGNED NOT NULL,
    wiki_id         VARCHAR(32)     NOT NULL,
    page_id         BIGINT UNSIGNED NOT NULL,
    coverage_start  DATETIME(6)     NOT NULL,
    coverage_end    DATETIME(6)     NOT NULL,
    complete        BOOLEAN         NOT NULL DEFAULT TRUE,
    PRIMARY KEY (build_id, wiki_id, page_id, coverage_start)
) ENGINE=InnoDB;

-- Historical projection: native MariaDB system-versioned table whose system period is
-- explicitly backfilled with the upstream Wikipedia edit timeline
-- (SET SESSION system_versioning_insert_history = ON in the importer session only).
CREATE TABLE IF NOT EXISTS wiki_snapshot (
    build_id           BIGINT UNSIGNED NOT NULL,
    wiki_id            VARCHAR(32)     NOT NULL,
    page_id            BIGINT UNSIGNED NOT NULL,
    revision_id        BIGINT UNSIGNED NOT NULL,
    content_available  BOOLEAN         NOT NULL,
    row_start TIMESTAMP(6) GENERATED ALWAYS AS ROW START,
    row_end   TIMESTAMP(6) GENERATED ALWAYS AS ROW END,
    PERIOD FOR SYSTEM_TIME (row_start, row_end),
    PRIMARY KEY (build_id, wiki_id, page_id)
) ENGINE=InnoDB WITH SYSTEM VERSIONING;

-- Derived (normalized) text per revision, kept separate from raw wikitext.
CREATE TABLE IF NOT EXISTS wiki_revision_text (
    wiki_id         VARCHAR(32)     NOT NULL,
    revision_id     BIGINT UNSIGNED NOT NULL,
    parser_version  VARCHAR(64)     NOT NULL,
    sections        LONGTEXT COMPRESSED NOT NULL COMMENT 'JSON [{heading,level,paragraphs[]}]',
    PRIMARY KEY (wiki_id, revision_id, parser_version)
) ENGINE=InnoDB;

-- Deduplicated normalized chunks with embeddings (one row per unique text/parser/model).
-- Dimension 384 = sentence-transformers/all-MiniLM-L6-v2 (and the 384-d test hash embedder).
-- A model with another dimension requires a fresh table/index + build.
CREATE TABLE IF NOT EXISTS wiki_chunk (
    chunk_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    text_hash       CHAR(64)        NOT NULL,
    parser_version  VARCHAR(64)     NOT NULL,
    embed_model     VARCHAR(128)    NOT NULL,
    text            MEDIUMTEXT      NOT NULL,
    token_estimate  INT UNSIGNED    NOT NULL,
    embedding       VECTOR(384)     NOT NULL,
    PRIMARY KEY (chunk_id),
    UNIQUE KEY uq_chunk (text_hash, parser_version, embed_model),
    VECTOR INDEX idx_embedding (embedding) M=16 DISTANCE=cosine
) ENGINE=InnoDB;

-- Revision -> chunk membership (shared text maps back to each revision separately).
CREATE TABLE IF NOT EXISTS wiki_revision_chunk (
    wiki_id          VARCHAR(32)     NOT NULL,
    revision_id      BIGINT UNSIGNED NOT NULL,
    ordinal          INT UNSIGNED    NOT NULL,
    chunk_id         BIGINT UNSIGNED NOT NULL,
    parser_version   VARCHAR(64)     NOT NULL,
    embed_model      VARCHAR(128)    NOT NULL,
    section_heading  VARCHAR(512)    NOT NULL,
    PRIMARY KEY (wiki_id, revision_id, ordinal),
    KEY idx_chunk (chunk_id, wiki_id, revision_id)
) ENGINE=InnoDB;

-- Corpus-wide topic search (demonstrates the HNSW ANN index on a real corpus):
-- passages of the English articles of MariaDB's "S" dataset (FineWiki, Aug 2025).
CREATE TABLE IF NOT EXISTS corpus_passage (
    passage_id   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    dataset      VARCHAR(64)     NOT NULL,
    lang         VARCHAR(8)      NOT NULL,
    page_id      BIGINT UNSIGNED NOT NULL,
    rev_id       BIGINT UNSIGNED NULL,
    title        VARCHAR(512)    NOT NULL,
    ordinal      INT UNSIGNED    NOT NULL,
    text         MEDIUMTEXT      NOT NULL,
    embed_model  VARCHAR(128)    NOT NULL,
    embedding    VECTOR(384)     NOT NULL,
    PRIMARY KEY (passage_id),
    UNIQUE KEY uq_passage (dataset, lang, page_id, ordinal, embed_model),
    VECTOR INDEX idx_corpus_embedding (embedding) M=16 DISTANCE=cosine
) ENGINE=InnoDB;

-- ============================================================ Bitemporal claims (stretch)
-- Domain: officeholder tenures ("who held <office> when"), extracted per revision.
-- Application time  = when the claim says the tenure applied: PERIOD FOR valid_period [valid_from, valid_to).
-- System time       = when that claim appeared in Wikipedia: historical system period backfilled with
--                     the revision timeline of the published snapshot build.
-- Unknown/open dates are explicit: precision ENUM + sentinels 0001-01-01 (unknown start) and
-- 9999-12-31 (ongoing or unknown end), never silently inferred.

CREATE TABLE IF NOT EXISTS wiki_claim (
    claim_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    claim_key       CHAR(64)        NOT NULL,
    wiki_id         VARCHAR(32)     NOT NULL,
    page_id         BIGINT UNSIGNED NOT NULL,
    office          VARCHAR(255)    NOT NULL,
    holder          VARCHAR(255)    NOT NULL,
    valid_from      DATE            NOT NULL,
    valid_to        DATE            NOT NULL,
    from_precision  ENUM('day','month','year','unknown') NOT NULL,
    to_precision    ENUM('day','month','year','unknown') NOT NULL,
    end_status      ENUM('ended','ongoing','unknown')    NOT NULL,
    PRIMARY KEY (claim_id),
    UNIQUE KEY uq_claim (claim_key),
    KEY idx_page (wiki_id, page_id),
    CHECK (valid_from < valid_to)
) ENGINE=InnoDB;

-- Which revision asserts which claim, and the exact excerpt supporting it.
CREATE TABLE IF NOT EXISTS wiki_revision_claim (
    wiki_id      VARCHAR(32)     NOT NULL,
    revision_id  BIGINT UNSIGNED NOT NULL,
    claim_id     BIGINT UNSIGNED NOT NULL,
    extractor    VARCHAR(96)     NOT NULL COMMENT 'infobox-v1 | llm:<model>:<prompt version>',
    source_ref   VARCHAR(64)     NOT NULL COMMENT 'infobox | chunk:<chunk_id>',
    excerpt      TEXT            NOT NULL,
    PRIMARY KEY (wiki_id, revision_id, claim_id, extractor, source_ref),
    KEY idx_claim (claim_id)
) ENGINE=InnoDB;

-- LLM extraction cache: one call per unique chunk and extractor version (shared text is extracted once).
CREATE TABLE IF NOT EXISTS wiki_claim_extraction (
    chunk_id      BIGINT UNSIGNED NOT NULL,
    extractor     VARCHAR(96)     NOT NULL,
    status        ENUM('ok','error') NOT NULL,
    accepted      INT UNSIGNED    NOT NULL DEFAULT 0,
    rejected      INT UNSIGNED    NOT NULL DEFAULT 0,
    result        MEDIUMTEXT      NULL COMMENT 'validated claims JSON + rejection reasons',
    created_at    DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (chunk_id, extractor)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS wiki_claim_build (
    claim_build_id  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    build_id        BIGINT UNSIGNED NOT NULL,
    fingerprint     CHAR(64)        NOT NULL,
    extractors      VARCHAR(255)    NOT NULL,
    status          ENUM('building','published','failed','superseded') NOT NULL,
    rows_written    INT UNSIGNED    NOT NULL DEFAULT 0,
    validation      JSON            NULL,
    created_at      DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (claim_build_id),
    KEY idx_build (build_id, status)
) ENGINE=InnoDB;

-- The bitemporal table: application-time period + system versioning.
CREATE TABLE IF NOT EXISTS wiki_claim_snapshot (
    claim_build_id  BIGINT UNSIGNED NOT NULL,
    wiki_id         VARCHAR(32)     NOT NULL,
    page_id         BIGINT UNSIGNED NOT NULL,
    claim_id        BIGINT UNSIGNED NOT NULL,
    office          VARCHAR(255)    NOT NULL,
    holder          VARCHAR(255)    NOT NULL,
    valid_from      DATE            NOT NULL,
    valid_to        DATE            NOT NULL,
    from_precision  ENUM('day','month','year','unknown') NOT NULL,
    to_precision    ENUM('day','month','year','unknown') NOT NULL,
    end_status      ENUM('ended','ongoing','unknown')    NOT NULL,
    PERIOD FOR valid_period (valid_from, valid_to),
    row_start TIMESTAMP(6) GENERATED ALWAYS AS ROW START,
    row_end   TIMESTAMP(6) GENERATED ALWAYS AS ROW END,
    PERIOD FOR SYSTEM_TIME (row_start, row_end),
    PRIMARY KEY (claim_build_id, wiki_id, page_id, claim_id),
    KEY idx_valid (claim_build_id, wiki_id, page_id, valid_from, valid_to)
) ENGINE=InnoDB WITH SYSTEM VERSIONING;
