<?php

namespace MediaWiki\Extension\WikiTimeMachine;

use MediaWiki\Html\Html;
use MediaWiki\SpecialPage\SpecialPage;

/**
 * Special:WikiTimeMachine -- pick an article and two UTC times, see both snapshots, the diff,
 * and ask a question answered from those two revisions only.
 */
class SpecialWikiTimeMachine extends SpecialPage {
	private ServiceClient $client;

	public function __construct( ServiceClient $client ) {
		parent::__construct( 'WikiTimeMachine' );
		$this->client = $client;
	}

	public function execute( $subPage ) {
		$this->setHeaders();
		$this->outputHeader();
		$out = $this->getOutput();
		$req = $this->getRequest();
		$out->addModules( 'ext.wikiTimeMachine' );
		$out->addModuleStyles( [ 'codex-styles' ] );

		$state = [
			'page' => $req->getInt( 'page' ) ?: null,
			'wiki' => preg_replace( '/[^a-z0-9_]/', '', $req->getText( 'wiki' ) ) ?: $this->client->getWikiId(),
			'before' => TimeInput::normalize( $req->getText( 'before' ) ),
			'after' => TimeInput::normalize( $req->getText( 'after' ) ),
			'question' => mb_substr( $req->getText( 'q' ), 0, 500 ),
			'validat' => preg_match( '/^\d{4}-\d{2}-\d{2}$/', $req->getText( 'validat' ) ) ? $req->getText( 'validat' ) : null,
		];

		$articles = [];
		$error = null;
		try {
			$articles = $this->client->articles()['articles'] ?? [];
		} catch ( ServiceException $e ) {
			$error = $e->getMessage();
		}
		$out->addJsConfigVars( 'wgWikiTimeMachine', [
			'state' => $state,
			'articles' => $articles,
			'examples' => $this->getConfig()->get( 'WikiTimeMachineExamples' ),
		] );

		$out->addHTML( Html::element( 'p', [ 'class' => 'wtm-intro' ], $this->msg( 'wikitimemachine-intro' )->text() ) );
		if ( $error !== null ) {
			$out->addHTML( Html::errorBox( $this->msg( 'wikitimemachine-compare-error', $error )->escaped() ) );
		}
		$out->addHTML( Html::rawElement( 'noscript', [], Html::warningBox( $this->msg( 'wikitimemachine-noscript' )->escaped() ) ) );
		$out->addHTML( $this->renderForm( $articles, $state ) );
		$out->addHTML( Html::rawElement( 'div', [ 'id' => 'wtm-examples', 'class' => 'wtm-examples' ], '' ) );
		$out->addHTML( Html::rawElement( 'section', [
			'id' => 'wtm-timeline', 'class' => 'wtm-timeline', 'hidden' => true,
			'aria-labelledby' => 'wtm-timeline-heading',
		],
			Html::element( 'h2', [ 'id' => 'wtm-timeline-heading' ], $this->msg( 'wikitimemachine-timeline-heading' )->text() ) .
			Html::element( 'p', [ 'id' => 'wtm-timeline-help', 'class' => 'wtm-hint' ], $this->msg( 'wikitimemachine-timeline-help' )->text() ) .
			Html::element( 'div', [ 'id' => 'wtm-timeline-status', 'role' => 'status' ], '' ) .
			Html::element( 'div', [ 'id' => 'wtm-timeline-track', 'class' => 'wtm-timeline-track' ], '' ) .
			Html::rawElement( 'div', [ 'id' => 'wtm-timeline-controls', 'hidden' => true ],
				Html::element( 'label', [ 'for' => 'wtm-timeline-slider' ], $this->msg( 'wikitimemachine-timeline-select' )->text() ) .
				Html::element( 'input', [ 'id' => 'wtm-timeline-slider', 'type' => 'range', 'min' => 0, 'max' => 0,
					'step' => 1, 'value' => 0, 'aria-describedby' => 'wtm-timeline-help' ] ) .
				Html::rawElement( 'div', [ 'class' => 'wtm-timeline-actions' ],
					Html::element( 'button', [ 'id' => 'wtm-timeline-prev', 'type' => 'button', 'class' => 'cdx-button' ], $this->msg( 'wikitimemachine-timeline-prev' )->text() ) .
					Html::element( 'button', [ 'id' => 'wtm-timeline-next', 'type' => 'button', 'class' => 'cdx-button' ], $this->msg( 'wikitimemachine-timeline-next' )->text() )
				) .
				Html::element( 'div', [ 'id' => 'wtm-timeline-selected', 'aria-live' => 'polite' ], '' ) .
				Html::rawElement( 'div', [ 'class' => 'wtm-timeline-actions' ],
					Html::element( 'button', [ 'id' => 'wtm-timeline-before', 'type' => 'button', 'class' => 'cdx-button' ], $this->msg( 'wikitimemachine-timeline-before' )->text() ) .
					Html::element( 'button', [ 'id' => 'wtm-timeline-after', 'type' => 'button', 'class' => 'cdx-button' ], $this->msg( 'wikitimemachine-timeline-after' )->text() )
				)
			) .
			Html::rawElement( 'details', [ 'id' => 'wtm-evolution', 'class' => 'wtm-evolution', 'hidden' => true ],
				Html::element( 'summary', [], $this->msg( 'wikitimemachine-evolution-heading' )->text() ) .
				Html::element( 'p', [ 'class' => 'wtm-hint' ], $this->msg( 'wikitimemachine-evolution-help' )->text() ) .
				Html::element( 'div', [ 'id' => 'wtm-evolution-events' ], '' )
			)
		) );
		$out->addHTML( Html::rawElement( 'div', [ 'id' => 'wtm-status', 'class' => 'wtm-status', 'role' => 'status', 'aria-live' => 'polite' ], '' ) );
		$out->addHTML( Html::rawElement( 'div', [ 'id' => 'wtm-sides', 'class' => 'wtm-sides' ], '' ) );
		$out->addHTML( Html::rawElement( 'section', [ 'id' => 'wtm-ask', 'class' => 'wtm-ask', 'hidden' => true ],
			Html::element( 'label', [ 'for' => 'wtm-question' ], $this->msg( 'wikitimemachine-question' )->text() ) .
			Html::rawElement( 'div', [ 'class' => 'wtm-ask-row' ],
				Html::element( 'input', [
					'id' => 'wtm-question', 'type' => 'text', 'maxlength' => 500, 'value' => $state['question'],
					'placeholder' => $this->msg( 'wikitimemachine-question-placeholder' )->text(),
				] ) .
				Html::element( 'button', [ 'id' => 'wtm-ask-button', 'type' => 'button', 'class' => 'cdx-button cdx-button--action-progressive cdx-button--weight-primary' ],
					$this->msg( 'wikitimemachine-ask' )->text() )
			) .
			Html::rawElement( 'div', [ 'id' => 'wtm-answer', 'class' => 'wtm-answer', 'aria-live' => 'polite' ], '' )
		) );
		$out->addHTML( Html::rawElement( 'section', [ 'id' => 'wtm-claims', 'class' => 'wtm-claims', 'hidden' => true ],
			Html::element( 'h2', [], $this->msg( 'wikitimemachine-claims-heading' )->text() ) .
			Html::element( 'p', [ 'class' => 'wtm-hint' ], $this->msg( 'wikitimemachine-claims-intro' )->text() ) .
			Html::rawElement( 'div', [ 'class' => 'wtm-ask-row' ],
				Html::element( 'label', [ 'for' => 'wtm-validat' ], $this->msg( 'wikitimemachine-claims-validat' )->text() ) .
				Html::element( 'input', [ 'id' => 'wtm-validat', 'type' => 'date', 'value' => $state['validat'] ?? '' ] ) .
				Html::element( 'button', [ 'id' => 'wtm-claims-button', 'type' => 'button', 'class' => 'cdx-button' ],
					$this->msg( 'wikitimemachine-claims-check' )->text() )
			) .
			Html::element( 'p', [ 'class' => 'wtm-hint' ], $this->msg( 'wikitimemachine-claims-validat-help' )->text() ) .
			Html::rawElement( 'div', [ 'id' => 'wtm-claims-result', 'aria-live' => 'polite' ], '' )
		) );
		$out->addHTML( Html::rawElement( 'section', [ 'id' => 'wtm-diff', 'class' => 'wtm-diff' ], '' ) );
	}

	private function renderForm( array $articles, array $state ): string {
		$options = Html::element( 'option', [ 'value' => '' ], $this->msg( 'wikitimemachine-pick-article' )->text() );
		foreach ( $articles as $a ) {
			$attrs = [ 'value' => (string)$a['page_id'], 'data-wiki' => $a['wiki_id'] ];
			if ( $state['page'] === (int)$a['page_id'] && $state['wiki'] === $a['wiki_id'] ) {
				$attrs['selected'] = true;
			}
			$options .= Html::element( 'option', $attrs, $a['title'] . ' (' . $a['wiki_id'] . ')' );
		}
		$field = function ( string $id, string $msg, string $input ): string {
			return Html::rawElement( 'div', [ 'class' => 'wtm-field' ],
				Html::element( 'label', [ 'for' => $id ], $this->msg( $msg )->text() ) . $input );
		};
		$dt = static function ( string $id, ?string $value ): string {
			$v = '';
			if ( $value !== null ) {
				$v = strlen( $value ) === 10 ? $value . 'T23:59:59' : substr( rtrim( $value, 'Z' ), 0, 19 );
			}
			return Html::element( 'input', [ 'id' => $id, 'name' => $id, 'type' => 'datetime-local', 'step' => 1, 'value' => $v ] );
		};
		return Html::rawElement( 'form', [ 'id' => 'wtm-form', 'class' => 'wtm-form', 'method' => 'get', 'action' => $this->getPageTitle()->getLocalURL() ],
			$field( 'wtm-article', 'wikitimemachine-article', Html::rawElement( 'select', [ 'id' => 'wtm-article', 'name' => 'page' ], $options ) ) .
			$field( 'wtm-before', 'wikitimemachine-before', $dt( 'wtm-before', $state['before'] ) ) .
			$field( 'wtm-after', 'wikitimemachine-after', $dt( 'wtm-after', $state['after'] ) ) .
			Html::rawElement( 'div', [ 'class' => 'wtm-field wtm-field-submit' ],
				Html::element( 'button', [ 'id' => 'wtm-compare-button', 'type' => 'submit', 'class' => 'cdx-button cdx-button--action-progressive cdx-button--weight-primary' ],
					$this->msg( 'wikitimemachine-compare' )->text() ) ) .
			Html::element( 'p', [ 'id' => 'wtm-windows', 'class' => 'wtm-hint' ], '' ) .
			Html::element( 'p', [ 'id' => 'wtm-validation', 'class' => 'wtm-validation', 'role' => 'alert' ], '' ) .
			Html::element( 'p', [ 'class' => 'wtm-hint' ], $this->msg( 'wikitimemachine-date-only-note' )->text() )
		);
	}

	protected function getGroupName() {
		return 'pages';
	}
}
