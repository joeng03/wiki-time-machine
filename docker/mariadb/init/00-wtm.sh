#!/bin/bash
# Creates the MediaWiki schema and the separate WikiTimeMachine app schema with
# two credentials: an importer (writes, historical SVT inserts) and a read-only runtime user.
set -euo pipefail
mariadb -uroot -p"${MARIADB_ROOT_PASSWORD}" <<SQL
CREATE DATABASE IF NOT EXISTS mediawiki CHARACTER SET binary;
CREATE USER IF NOT EXISTS 'mediawiki'@'%' IDENTIFIED BY '${MW_DB_PASSWORD}';
GRANT ALL PRIVILEGES ON mediawiki.* TO 'mediawiki'@'%';

CREATE DATABASE IF NOT EXISTS wtm CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS wtm_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'wtm_importer'@'%' IDENTIFIED BY '${WTM_IMPORTER_PASSWORD}';
CREATE USER IF NOT EXISTS 'wtm_reader'@'%' IDENTIFIED BY '${WTM_READER_PASSWORD}';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, INDEX, ALTER, CREATE TEMPORARY TABLES, LOCK TABLES ON wtm.* TO 'wtm_importer'@'%';
GRANT ALL PRIVILEGES ON wtm_test.* TO 'wtm_importer'@'%';
GRANT SELECT ON wtm.* TO 'wtm_reader'@'%';
GRANT SELECT ON wtm_test.* TO 'wtm_reader'@'%';
FLUSH PRIVILEGES;
SQL
