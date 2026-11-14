<?php

namespace MediaWiki\Extension\WikiTimeMachine;

use RuntimeException;

/**
 * Error returned by (or while calling) the retrieval service.
 */
class ServiceException extends RuntimeException {
	/** @var string Machine-readable code, e.g. "reversed_dates" */
	public string $errorCode;
	/** @var int HTTP status from the service (0 = transport failure) */
	public int $httpStatus;

	public function __construct( string $errorCode, string $message, int $httpStatus = 0 ) {
		parent::__construct( $message );
		$this->errorCode = $errorCode;
		$this->httpStatus = $httpStatus;
	}

	public function isClientError(): bool {
		return $this->httpStatus >= 400 && $this->httpStatus < 500;
	}
}
