#!/usr/bin/env bash
# Reproducible MediaWiki setup: pinned core + skin checkout, composer install, install.php, extension include.
set -euo pipefail
cd "$(dirname "$0")/.."
MW_TAG=1.43.11
VECTOR_BRANCH=REL1_43
set -a; source .env; set +a

if [ ! -d mediawiki/.git ]; then
  git clone --depth 1 --branch "$MW_TAG" https://gerrit.wikimedia.org/r/mediawiki/core.git mediawiki
fi
if [ ! -f mediawiki/skins/Vector/skin.json ]; then
  rm -rf mediawiki/skins/Vector
  git clone --depth 1 --branch "$VECTOR_BRANCH" https://gerrit.wikimedia.org/r/mediawiki/skins/Vector.git mediawiki/skins/Vector
fi
if [ ! -f mediawiki/vendor/autoload.php ]; then
  docker run --rm -v "$PWD/mediawiki":/app -w /app composer:2.8.12 install --no-interaction --no-progress --prefer-dist --ignore-platform-reqs
fi
mkdir -p mediawiki/extensions/WikiTimeMachine   # bind-mount target

docker compose up -d db service mediawiki
if [ ! -f mediawiki/LocalSettings.php ]; then
  docker compose exec -T mediawiki php maintenance/run.php install \
    --dbtype=mysql --dbserver=db --dbname=mediawiki --dbuser=mediawiki --dbpass="$MW_DB_PASSWORD" \
    --server="http://localhost:8080" --scriptpath="" --lang=en --skins=Vector \
    --pass="$MW_ADMIN_PASSWORD" "Wiki Time Machine" "Admin"
  docker compose exec -T mediawiki bash -c 'printf "\n// WikiTimeMachine (hackathon)\nrequire_once __DIR__ . \"/LocalSettings.wtm.php\";\n" >> LocalSettings.php'
  docker compose exec -T mediawiki chown www-data:www-data LocalSettings.php || true
fi
echo "MediaWiki ready: http://localhost:8080/index.php/Special:WikiTimeMachine"
