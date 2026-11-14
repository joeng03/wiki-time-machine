<?php
require __DIR__ . '/LocalSettings.wtm.php';
$wgDBserver = getenv( 'MW_DB_HOST' );
$wgDBpassword = getenv( 'MW_DB_PASSWORD' );
$wgServer = getenv( 'MW_SERVER' );
$wgCanonicalServer = $wgServer;
$wgWikiTimeMachineServiceUrl = getenv( 'WTM_SERVICE_URL' );
$wgShowExceptionDetails = false;
$wgCookieSecure = true;
// TLS terminates at Fly Proxy; the application is served only over HTTPS.
$wgForceHTTPS = true;
