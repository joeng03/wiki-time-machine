<?php

namespace MediaWiki\Extension\WikiTimeMachine\Api;

use MediaWiki\Extension\WikiTimeMachine\TimeInput;
use Wikimedia\ParamValidator\ParamValidator;

/**
 * Bitemporal officeholder claims: as of each side's Wikipedia time (system time), who did the article
 * say held the office on the given date (application time)?
 */
class ApiWtmClaims extends ApiWtmBase {
	public function execute() {
		$page = (int)$this->getParameter( 'page' );
		$before = $this->time( 'before' );
		$after = $this->time( 'after' );
		$validAt = (string)$this->getParameter( 'validat' );
		if ( !preg_match( '/^\d{4}-\d{2}-\d{2}$/', $validAt ) ) {
			$this->dieWithError( [ 'apierror-wikitimemachine-input', 'validat must be YYYY-MM-DD' ], 'wtm-invalid_date' );
		}
		if ( !TimeInput::isOrdered( $before, $after ) ) {
			$this->dieWithError( [ 'apierror-wikitimemachine-input', 'the first date must not be after the second date' ], 'wtm-reversed_dates' );
		}
		$this->addData( $this->call( fn () => $this->client->claims( $page, $before, $after, $validAt, $this->wiki() ) ) );
	}

	public function getAllowedParams() {
		return [
			'wiki' => self::wikiParam(),
			'page' => self::pageParam(),
			'before' => self::timeParam(),
			'after' => self::timeParam(),
			'validat' => [ ParamValidator::PARAM_TYPE => 'string', ParamValidator::PARAM_REQUIRED => true ],
		];
	}

	protected function getExamplesMessages() {
		return [
			'action=wtmclaims&page=231789&before=2023-06-15&after=2023-06-25&validat=2023-06-22&formatversion=2'
				=> 'apihelp-wtmclaims-summary',
		];
	}
}
