<?php

namespace MediaWiki\Extension\WikiTimeMachine\Api;

class ApiWtmSnapshot extends ApiWtmBase {
	public function execute() {
		$page = (int)$this->getParameter( 'page' );
		$at = $this->time( 'at' );
		$this->addData( $this->call( fn () => $this->client->snapshot( $page, $at, $this->wiki() ) ) );
	}

	public function getAllowedParams() {
		return [ 'wiki' => self::wikiParam(), 'page' => self::pageParam(), 'at' => self::timeParam() ];
	}
}
