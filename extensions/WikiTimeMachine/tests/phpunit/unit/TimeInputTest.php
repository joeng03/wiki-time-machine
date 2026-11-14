<?php

namespace MediaWiki\Extension\WikiTimeMachine\Tests\Unit;

use MediaWiki\Extension\WikiTimeMachine\TimeInput;
use MediaWikiUnitTestCase;

/**
 * @covers \MediaWiki\Extension\WikiTimeMachine\TimeInput
 */
class TimeInputTest extends MediaWikiUnitTestCase {
	public static function provideNormalize(): array {
		return [
			'date only stays date only' => [ '2021-12-15', '2021-12-15' ],
			'datetime gets Z' => [ '2023-04-20T12:00', '2023-04-20T12:00Z' ],
			'seconds with Z' => [ '2023-04-20T12:00:01Z', '2023-04-20T12:00:01Z' ],
			'explicit +00:00' => [ '2023-04-20T12:00:01+00:00', '2023-04-20T12:00:01Z' ],
			'microseconds' => [ '2023-04-20T12:00:01.123456Z', '2023-04-20T12:00:01.123456Z' ],
			'other offset rejected' => [ '2023-04-20T12:00:01+02:00', null ],
			'invalid date' => [ '2023-02-30', null ],
			'garbage' => [ '<script>', null ],
			'out of range' => [ '1999-01-01', null ],
		];
	}

	/**
	 * @dataProvider provideNormalize
	 */
	public function testNormalize( string $in, ?string $expected ) {
		$this->assertSame( $expected, TimeInput::normalize( $in ) );
	}

	public function testOrdering() {
		$this->assertTrue( TimeInput::isOrdered( '2021-12-15', '2023-04-20T00:00:00Z' ) );
		$this->assertTrue( TimeInput::isOrdered( '2021-12-15', '2021-12-15' ) );
		// date-only means end of day, so it is after a same-day time
		$this->assertFalse( TimeInput::isOrdered( '2021-12-15', '2021-12-15T12:00:00Z' ) );
		$this->assertFalse( TimeInput::isOrdered( '2023-04-20T00:00:00Z', '2021-12-15T00:00:00Z' ) );
	}
}
