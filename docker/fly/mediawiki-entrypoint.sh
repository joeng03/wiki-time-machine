#!/bin/bash
set -euo pipefail
cd /var/www/html
if [ ! -s /data/LocalSettings.php ]; then
    php maintenance/run.php install \
        --dbtype=mysql --dbserver="$MW_DB_HOST" --dbname=mediawiki \
        --dbuser=mediawiki --dbpass="$MW_DB_PASSWORD" \
        --server="$MW_SERVER" --scriptpath="" --lang=en --skins=Vector \
        --pass="$MW_ADMIN_PASSWORD" --confpath=/data \
        "Wiki Time Machine" "Admin"
    chown www-data:www-data /data/LocalSettings.php
    chmod 640 /data/LocalSettings.php
fi
# Keep install-generated keys on the volume and runtime overrides in the image.
printf '<?php\nrequire "/data/LocalSettings.php";\nrequire __DIR__ . "/LocalSettings.fly.php";\n' > LocalSettings.php
exec docker-php-entrypoint "$@"
