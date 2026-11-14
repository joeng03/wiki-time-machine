<?php

namespace MediaWiki\Extension\WikiTimeMachine\Api;

use MediaWiki\Extension\WikiTimeMachine\TimeInput;

class ApiWtmCompare extends ApiWtmBase {
	public function execute() {
		$page = (int)$this->getParameter( 'page' );
		$before = $this->time( 'before' );
		$after = $this->time( 'after' );
		if ( !TimeInput::isOrdered( $before, $after ) ) {
			$this->dieWithError( [ 'apierror-wikitimemachine-input', 'the first date must not be after the second date' ], 'wtm-reversed_dates' );
		}
		$this->addData( $this->call( fn () => $this->client->compare( $page, $before, $after, $this->wiki() ) ) );
	}

	public function getAllowedParams() {
		return [ 'wiki' => self::wikiParam(), 'page' => self::pageParam(), 'before' => self::timeParam(), 'after' => self::timeParam() ];
	}

	protected function getExamplesMessages() {
		return [
			'action=wtmcompare&page=1977274&before=2021-12-15&after=2023-04-20&formatversion=2' => 'apihelp-wtmcompare-summary',
		];
	}
}
