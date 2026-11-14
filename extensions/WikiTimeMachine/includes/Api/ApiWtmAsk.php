<?php

namespace MediaWiki\Extension\WikiTimeMachine\Api;

use MediaWiki\Extension\WikiTimeMachine\TimeInput;
use Wikimedia\ParamValidator\ParamValidator;

class ApiWtmAsk extends ApiWtmBase {
	public function execute() {
		$page = (int)$this->getParameter( 'page' );
		$before = $this->time( 'before' );
		$after = $this->time( 'after' );
		$question = trim( (string)$this->getParameter( 'question' ) );
		if ( mb_strlen( $question ) < 3 || mb_strlen( $question ) > 500 ) {
			$this->dieWithError( [ 'apierror-wikitimemachine-input', 'question must be 3-500 characters' ], 'wtm-invalid_question' );
		}
		if ( !TimeInput::isOrdered( $before, $after ) ) {
			$this->dieWithError( [ 'apierror-wikitimemachine-input', 'the first date must not be after the second date' ], 'wtm-reversed_dates' );
		}
		$this->addData( $this->call( fn () => $this->client->ask( $page, $before, $after, $question, $this->wiki() ) ) );
	}

	public function getAllowedParams() {
		return [
			'wiki' => self::wikiParam(), 'page' => self::pageParam(),
			'before' => self::timeParam(),
			'after' => self::timeParam(),
			'question' => [ ParamValidator::PARAM_TYPE => 'string', ParamValidator::PARAM_REQUIRED => true ],
		];
	}
}
