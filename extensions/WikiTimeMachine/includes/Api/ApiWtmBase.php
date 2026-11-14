<?php

namespace MediaWiki\Extension\WikiTimeMachine\Api;

use MediaWiki\Api\ApiBase;
use MediaWiki\Api\ApiMain;
use MediaWiki\Api\ApiResult;
use MediaWiki\Extension\WikiTimeMachine\ServiceClient;
use MediaWiki\Extension\WikiTimeMachine\ServiceException;
use MediaWiki\Extension\WikiTimeMachine\TimeInput;
use Wikimedia\ParamValidator\ParamValidator;

/**
 * Shared plumbing for the read-only WikiTimeMachine API modules.
 * Read permission is enforced by ApiMain (isReadMode() defaults to true).
 */
abstract class ApiWtmBase extends ApiBase {
	protected ServiceClient $client;

	public function __construct( ApiMain $main, string $action, ServiceClient $client ) {
		parent::__construct( $main, $action );
		$this->client = $client;
	}

	protected function addData( array $data ): void {
		$this->getResult()->addValue( null, $this->getModuleName(), $data, ApiResult::NO_SIZE_CHECK );
	}

	protected function time( string $name ): string {
		$raw = (string)$this->getParameter( $name );
		$norm = TimeInput::normalize( $raw );
		if ( $norm === null ) {
			$this->dieWithError( [ 'apierror-wikitimemachine-input', "invalid UTC time for '$name'" ], 'wtm-invalid_time' );
		}
		return $norm;
	}

	protected function call( callable $fn ): array {
		try {
			return $fn();
		} catch ( ServiceException $e ) {
			if ( $e->isClientError() ) {
				$this->dieWithError( [ 'apierror-wikitimemachine-input', $e->getMessage() ], 'wtm-' . $e->errorCode );
			}
			$this->dieWithError( [ 'apierror-wikitimemachine-service', $e->getMessage() ], 'wtm-' . $e->errorCode, null, 502 );
		}
	}

	protected function wiki(): ?string {
		$w = $this->getParameter( 'wiki' );
		return $w === null || $w === '' ? null : (string)$w;
	}

	protected static function wikiParam(): array {
		return [ ParamValidator::PARAM_TYPE => 'string', ParamValidator::PARAM_DEFAULT => '' ];
	}

	protected static function pageParam(): array {
		return [ ParamValidator::PARAM_TYPE => 'integer', ParamValidator::PARAM_REQUIRED => true ];
	}

	protected static function timeParam(): array {
		return [ ParamValidator::PARAM_TYPE => 'string', ParamValidator::PARAM_REQUIRED => true ];
	}
}
