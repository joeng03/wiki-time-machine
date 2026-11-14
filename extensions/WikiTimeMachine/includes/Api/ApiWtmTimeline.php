<?php

namespace MediaWiki\Extension\WikiTimeMachine\Api;

use MediaWiki\Extension\WikiTimeMachine\TimeInput;

class ApiWtmTimeline extends ApiWtmBase {
	public function execute() {
		$start = $this->time( 'start' );
		$end = $this->time( 'end' );
		if ( $start === $end || !TimeInput::isOrdered( $start, $end ) ) {
			$this->dieWithError( [ 'apierror-wikitimemachine-input', 'timeline start must be before its end' ], 'wtm-invalid_range' );
		}
		$this->addData( $this->call( fn () => $this->client->timeline(
			(int)$this->getParameter( 'page' ), $start, $end, $this->wiki()
		) ) );
	}

	public function getAllowedParams() {
		return [ 'wiki' => self::wikiParam(), 'page' => self::pageParam(), 'start' => self::timeParam(), 'end' => self::timeParam() ];
	}

	protected function getExamplesMessages() {
		return [
			'action=wtmtimeline&page=1977274&start=2021-12-01T00:00:00Z&end=2023-04-22T00:00:00Z&formatversion=2'
				=> 'apihelp-wtmtimeline-summary',
		];
	}
}
