<?php

namespace MediaWiki\Extension\WikiTimeMachine;

use MediaWiki\Json\FormatJson;
use MediaWiki\Http\HttpRequestFactory;

/**
 * Server-side client for the internal retrieval service. Browsers never talk to the service;
 * they use same-origin API modules that call this client.
 */
class ServiceClient {
	private HttpRequestFactory $http;
	private string $baseUrl;
	private int $timeout;
	private int $askTimeout;
	private string $wikiId;

	public function __construct(
		HttpRequestFactory $http, string $baseUrl, int $timeout, int $askTimeout, string $wikiId
	) {
		$this->http = $http;
		$this->baseUrl = rtrim( $baseUrl, '/' );
		$this->timeout = $timeout;
		$this->askTimeout = $askTimeout;
		$this->wikiId = $wikiId;
	}

	public function getWikiId(): string {
		return $this->wikiId;
	}

	public function articles( string $query = '' ): array {
		return $this->request( 'GET', '/articles', [ 'q' => $query ], null, $this->timeout );
	}

	public function snapshot( int $pageId, string $at, ?string $wikiId = null ): array {
		return $this->request( 'GET', '/snapshot', [
			'wiki_id' => $wikiId ?? $this->wikiId, 'page_id' => $pageId, 'at' => $at,
		], null, $this->timeout );
	}

	public function compare( int $pageId, string $before, string $after, ?string $wikiId = null ): array {
		return $this->request( 'GET', '/compare', [
			'wiki_id' => $wikiId ?? $this->wikiId, 'page_id' => $pageId, 'before' => $before, 'after' => $after,
		], null, $this->timeout );
	}

	public function ask( int $pageId, string $before, string $after, string $question, ?string $wikiId = null ): array {
		return $this->request( 'POST', '/ask', [], [
			'wiki_id' => $wikiId ?? $this->wikiId, 'page_id' => $pageId, 'before' => $before, 'after' => $after,
			'question' => $question,
		], $this->askTimeout );
	}

	/**
	 * @throws ServiceException
	 */
	private function request( string $method, string $path, array $query, ?array $body, int $timeout ): array {
		$url = $this->baseUrl . $path . ( $query ? '?' . http_build_query( $query ) : '' );
		$options = [ 'method' => $method, 'timeout' => $timeout, 'connectTimeout' => 3 ];
		if ( $body !== null ) {
			$options['postData'] = FormatJson::encode( $body );
		}
		$req = $this->http->create( $url, $options, __METHOD__ );
		if ( $body !== null ) {
			$req->setHeader( 'Content-Type', 'application/json' );
		}
		$req->setHeader( 'Accept', 'application/json' );
		$status = $req->execute();
		$code = (int)$req->getStatus();
		$content = (string)$req->getContent();
		$data = $content !== '' ? json_decode( $content, true ) : null;
		if ( $code >= 200 && $code < 300 && is_array( $data ) ) {
			return $data;
		}
		if ( is_array( $data ) && isset( $data['detail'] ) ) {
			$detail = $data['detail'];
			if ( is_array( $detail ) && isset( $detail['code'] ) ) {
				throw new ServiceException( (string)$detail['code'], (string)( $detail['message'] ?? $detail['code'] ), $code );
			}
			throw new ServiceException( 'invalid_request', is_string( $detail ) ? $detail : 'invalid request', $code );
		}
		$reason = $code ? "HTTP $code" : 'service unreachable';
		if ( !$status->isOK() && !$code ) {
			$reason = 'service unreachable or timed out';
		}
		throw new ServiceException( 'service_error', $reason, $code );
	}
}
