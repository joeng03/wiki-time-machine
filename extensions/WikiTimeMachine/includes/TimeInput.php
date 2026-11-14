<?php

namespace MediaWiki\Extension\WikiTimeMachine;

/**
 * Validation/normalisation of user-supplied UTC times before they reach the service.
 * Accepted: YYYY-MM-DD (end of day UTC, applied by the service) or YYYY-MM-DDTHH:MM[:SS[.ffffff]][Z].
 * Values without an explicit zone are interpreted as UTC; other offsets are rejected.
 */
class TimeInput {
	private const PATTERN = '/^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|\+00:00)?)?$/';

	public static function isValid( string $value ): bool {
		if ( !preg_match( self::PATTERN, $value ) ) {
			return false;
		}
		[ $y, $m, $d ] = array_map( 'intval', explode( '-', substr( $value, 0, 10 ) ) );
		return checkdate( $m, $d, $y ) && $y >= 2001 && $y <= 2100;
	}

	/**
	 * @return string|null Normalised value (with trailing Z when a time is present) or null if invalid.
	 */
	public static function normalize( string $value ): ?string {
		$value = trim( $value );
		if ( !self::isValid( $value ) ) {
			return null;
		}
		if ( strlen( $value ) === 10 ) {
			return $value;
		}
		if ( str_ends_with( $value, '+00:00' ) ) {
			$value = substr( $value, 0, -6 );
		}
		return str_ends_with( $value, 'Z' ) ? $value : $value . 'Z';
	}

	public static function isOrdered( string $before, string $after ): bool {
		// Normalised strings compare chronologically except date-only (end of day) vs same-day time.
		$b = strlen( $before ) === 10 ? $before . 'T23:59:59.999999Z' : $before;
		$a = strlen( $after ) === 10 ? $after . 'T23:59:59.999999Z' : $after;
		return strtotime( rtrim( $b, 'Z' ) . ' UTC' ) <= strtotime( rtrim( $a, 'Z' ) . ' UTC' );
	}
}
