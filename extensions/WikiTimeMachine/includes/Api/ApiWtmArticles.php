<?php

namespace MediaWiki\Extension\WikiTimeMachine\Api;

use Wikimedia\ParamValidator\ParamValidator;

class ApiWtmArticles extends ApiWtmBase {
	public function execute() {
		$query = (string)$this->getParameter( 'query' );
		$this->addData( $this->call( fn () => $this->client->articles( $query ) ) );
	}

	public function getAllowedParams() {
		return [ 'query' => [ ParamValidator::PARAM_TYPE => 'string', ParamValidator::PARAM_DEFAULT => '' ] ];
	}

	protected function getExamplesMessages() {
		return [ 'action=wtmarticles&formatversion=2' => 'apihelp-wtmarticles-summary' ];
	}
}
