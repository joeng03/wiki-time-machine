<?php

use MediaWiki\Extension\WikiTimeMachine\ServiceClient;
use MediaWiki\MediaWikiServices;

/** @phpcs-require-sorted-array */
return [
	'WikiTimeMachine.ServiceClient' => static function ( MediaWikiServices $services ): ServiceClient {
		$config = $services->getMainConfig();
		return new ServiceClient(
			$services->getHttpRequestFactory(),
			$config->get( 'WikiTimeMachineServiceUrl' ),
			(int)$config->get( 'WikiTimeMachineServiceTimeout' ),
			(int)$config->get( 'WikiTimeMachineAskTimeout' ),
			$config->get( 'WikiTimeMachineDefaultWiki' )
		);
	},
];
