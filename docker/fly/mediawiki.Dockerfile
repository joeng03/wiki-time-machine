FROM mediawiki:1.43.11
COPY extensions/WikiTimeMachine /var/www/html/extensions/WikiTimeMachine
COPY docker/mediawiki/LocalSettings.wtm.php /var/www/html/LocalSettings.wtm.php
COPY docker/fly/LocalSettings.fly.php /var/www/html/LocalSettings.fly.php
COPY docker/fly/mediawiki-entrypoint.sh /usr/local/bin/wtm-entrypoint
RUN chmod +x /usr/local/bin/wtm-entrypoint
ENTRYPOINT ["wtm-entrypoint"]
CMD ["apache2-foreground"]
