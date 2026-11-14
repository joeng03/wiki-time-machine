/*!
 * Special:WikiTimeMachine client. All untrusted text (Wikipedia content, model output) is
 * inserted with textContent / jQuery .text(); never as HTML.
 */
( function () {
	'use strict';

	var cfg = mw.config.get( 'wgWikiTimeMachine' ) || {},
		api = new mw.Api(),
		state = cfg.state || {},
		articles = cfg.articles || [],
		$form, $article, $before, $after, $compareBtn, $validation, $windows,
		$status, $sides, $diff, $ask, $question, $askBtn, $answer,
		lastCompare = null;

	function el( tag, cls, text ) {
		var node = document.createElement( tag );
		if ( cls ) {
			node.className = cls;
		}
		if ( text !== undefined && text !== null ) {
			node.textContent = String( text );
		}
		return node;
	}

	function link( href, text ) {
		var a = el( 'a', null, text );
		// Only permanent Wikipedia revision links come back from the service; enforce https.
		if ( /^https:\/\/[a-z-]+\.wikipedia\.org\//.test( href ) ) {
			a.href = href;
			a.rel = 'noopener';
			a.target = '_blank';
		}
		return a;
	}

	/** datetime-local value -> UTC ISO string accepted by the API. */
	function toUtc( v ) {
		if ( !v ) {
			return '';
		}
		if ( /^\d{4}-\d{2}-\d{2}$/.test( v ) ) {
			return v;
		}
		return ( v.length === 16 ? v + ':00' : v ) + 'Z';
	}

	function fromUtc( v ) {
		if ( !v ) {
			return '';
		}
		if ( /^\d{4}-\d{2}-\d{2}$/.test( v ) ) {
			return v + 'T23:59:59';
		}
		return v.replace( /Z$/, '' ).slice( 0, 19 );
	}

	function fmt( iso ) {
		return iso ? iso.replace( 'T', ' ' ).replace( /\.\d+Z$/, 'Z' ).replace( 'Z', ' UTC' ) : '';
	}

	function wikiOf() {
		var a = selectedArticle();
		return a ? a.wiki_id : '';
	}

	function selectedArticle() {
		var id = parseInt( $article.val(), 10 ),
			wiki = $article.find( 'option:selected' ).attr( 'data-wiki' );
		return articles.filter( function ( a ) {
			return a.page_id === id && a.wiki_id === wiki;
		} )[ 0 ] || null;
	}

	function validate() {
		var b = toUtc( $before.val() ), a = toUtc( $after.val() ), msg = '';
		var art = selectedArticle();
		$windows.text( art ? mw.msg( 'wikitimemachine-windows', art.windows.map( function ( w ) {
			return fmt( w.start ) + ' – ' + fmt( w.end );
		} ).join( '; ' ) ) : '' );
		if ( b && a && b.replace( /^(\d{4}-\d{2}-\d{2})$/, '$1T23:59:59.999999Z' ) > a.replace( /^(\d{4}-\d{2}-\d{2})$/, '$1T23:59:59.999999Z' ) ) {
			msg = mw.msg( 'wikitimemachine-reversed' );
		}
		$validation.text( msg );
		var ok = !msg && art && b && a;
		$compareBtn.prop( 'disabled', !ok );
		$askBtn.prop( 'disabled', !ok || !lastCompare );
		return ok;
	}

	function syncUrl() {
		var url = new URL( window.location.href );
		[ [ 'wiki', wikiOf() ], [ 'page', $article.val() ], [ 'before', toUtc( $before.val() ) ], [ 'after', toUtc( $after.val() ) ],
			[ 'q', $question.val() ] ].forEach( function ( kv ) {
			if ( kv[ 1 ] ) {
				url.searchParams.set( kv[ 0 ], kv[ 1 ] );
			} else {
				url.searchParams.delete( kv[ 0 ] );
			}
		} );
		window.history.replaceState( null, '', url.toString() );
	}

	function errorText( code, result ) {
		if ( result && result.error && result.error.info ) {
			return result.error.info;
		}
		return code || 'error';
	}

	// ---------------------------------------------------------------- sides

	function renderSide( label, snap, dateOnly ) {
		var card = el( 'article', 'wtm-side wtm-side-' + ( snap.status || 'error' ) );
		card.appendChild( el( 'h3', null, label ) );
		card.appendChild( el( 'p', 'wtm-requested', mw.msg( 'wikitimemachine-requested', fmt( snap.requested_at ) ) +
			( dateOnly ? ' *' : '' ) ) );
		if ( snap.revision_id ) {
			var p = el( 'p', 'wtm-resolved' );
			p.appendChild( el( 'strong', null, mw.msg( 'wikitimemachine-resolved', snap.revision_id, fmt( snap.source_timestamp ) ) ) );
			card.appendChild( p );
			var lp = el( 'p' );
			lp.appendChild( link( snap.url, mw.msg( 'wikitimemachine-open-revision' ) + ' (oldid=' + snap.revision_id + ')' ) );
			card.appendChild( lp );
		}
		if ( snap.status !== 'ok' ) {
			var key = 'wikitimemachine-status-' + snap.status;
			// eslint-disable-next-line mediawiki/msg-doc
			card.appendChild( el( 'p', 'wtm-empty', mw.message( key ).exists() ? mw.msg( key ) : snap.message ) );
		}
		return card;
	}

	// ---------------------------------------------------------------- diff

	function heading( h ) {
		return h || mw.msg( 'wikitimemachine-lead' );
	}

	function renderParagraph( p ) {
		var div = el( 'div', 'wtm-para wtm-para-' + p.status );
		if ( p.status === 'changed' ) {
			p.words.forEach( function ( w ) {
				if ( w.op === 'eq' ) {
					div.appendChild( document.createTextNode( w.text ) );
				} else {
					div.appendChild( el( w.op === 'del' ? 'del' : 'ins', null, w.text ) );
				}
			} );
		} else if ( p.status === 'added' ) {
			div.appendChild( el( 'ins', null, p.after ) );
		} else if ( p.status === 'removed' ) {
			div.appendChild( el( 'del', null, p.before ) );
		} else {
			div.textContent = p.after;
		}
		return div;
	}

	function renderDiff( data ) {
		$diff.empty();
		var d = data.diff, root = $diff[ 0 ];
		if ( !d ) {
			return;
		}
		root.appendChild( el( 'h2', null, mw.msg( 'wikitimemachine-change-heading' ) ) );
		var links = el( 'p', 'wtm-difflinks' );
		links.appendChild( link( data.before.url, 'oldid=' + data.before.revision_id ) );
		links.appendChild( document.createTextNode( ' → ' ) );
		links.appendChild( link( data.after.url, 'oldid=' + data.after.revision_id ) );
		root.appendChild( links );
		root.appendChild( el( 'p', 'wtm-hint', mw.msg( 'wikitimemachine-extracted-note' ) ) );
		if ( d.same_revision ) {
			root.appendChild( el( 'p', 'wtm-empty', mw.msg( 'wikitimemachine-same-revision' ) ) );
		}
		if ( d.identical ) {
			root.appendChild( el( 'p', 'wtm-empty', mw.msg( 'wikitimemachine-no-change' ) ) );
			return;
		}
		var s = d.stats;
		root.appendChild( el( 'p', 'wtm-stats', mw.msg( 'wikitimemachine-diff-stats', s.paragraphs_changed,
			s.paragraphs_added, s.paragraphs_removed, s.paragraphs_changed + s.paragraphs_added + s.paragraphs_removed ) ) );
		d.sections.forEach( function ( sec ) {
			if ( sec.status === 'same' ) {
				return;
			}
			var box = el( 'section', 'wtm-sec wtm-sec-' + sec.status );
			var title = sec.status === 'added' ? heading( sec.heading_after ) + ' — ' + mw.msg( 'wikitimemachine-section-added' ) :
				sec.status === 'removed' ? heading( sec.heading_before ) + ' — ' + mw.msg( 'wikitimemachine-section-removed' ) :
					heading( sec.heading_after );
			box.appendChild( el( 'h3', null, title ) );
			var run = [];
			function flush() {
				if ( !run.length ) {
					return;
				}
				var det = el( 'details', 'wtm-unchanged' );
				det.appendChild( el( 'summary', null, mw.msg( 'wikitimemachine-unchanged-collapsed', run.length ) ) );
				run.forEach( function ( p ) {
					det.appendChild( renderParagraph( p ) );
				} );
				box.appendChild( det );
				run = [];
			}
			sec.paragraphs.forEach( function ( p ) {
				if ( p.status === 'same' ) {
					run.push( p );
				} else {
					flush();
					box.appendChild( renderParagraph( p ) );
				}
			} );
			flush();
			root.appendChild( box );
		} );
	}

	function compare() {
		if ( !validate() ) {
			return;
		}
		syncUrl();
		$status.text( mw.msg( 'wikitimemachine-loading' ) );
		$sides.empty();
		$diff.empty();
		$answer.empty();
		lastCompare = null;
		$ask.prop( 'hidden', true );
		api.get( {
			action: 'wtmcompare', formatversion: 2, wiki: wikiOf(),
			page: $article.val(), before: toUtc( $before.val() ), after: toUtc( $after.val() )
		}, { timeout: 30000 } ).then( function ( res ) {
			var data = res.wtmcompare;
			lastCompare = data;
			$status.text( '' );
			$sides.append(
				renderSide( mw.msg( 'wikitimemachine-side-before' ), data.before, data.date_only_end_of_day.before ),
				renderSide( mw.msg( 'wikitimemachine-side-after' ), data.after, data.date_only_end_of_day.after )
			);
			renderDiff( data );
			$ask.prop( 'hidden', false );
			validate();
			if ( state.question && $question.val() === state.question && state.autoAsk ) {
				state.autoAsk = false;
				ask();
			}
		}, function ( code, result ) {
			$status.text( mw.msg( 'wikitimemachine-compare-error', errorText( code, result ) ) );
		} );
	}

	// ---------------------------------------------------------------- ask

	function citationList( citations ) {
		var wrap = el( 'span', 'wtm-cites' );
		citations.forEach( function ( c ) {
			var a = link( c.url, '[' + c.id + ']' );
			a.title = ( c.section || mw.msg( 'wikitimemachine-lead' ) ) + ': ' + c.excerpt.slice( 0, 300 );
			wrap.appendChild( document.createTextNode( ' ' ) );
			wrap.appendChild( a );
		} );
		return wrap;
	}

	function evidenceList( items ) {
		var ol = el( 'ol', 'wtm-evidence' );
		items.forEach( function ( s ) {
			var li = el( 'li' );
			var head = el( 'div', 'wtm-ev-head' );
			head.appendChild( el( 'strong', null, '[' + s.id + '] ' ) );
			head.appendChild( document.createTextNode( ( s.section || mw.msg( 'wikitimemachine-lead' ) ) + ' · ' ) );
			head.appendChild( link( s.url, 'oldid=' + s.revision_id ) );
			head.appendChild( el( 'span', 'wtm-dist', ' · cos ' + s.distance ) );
			li.appendChild( head );
			li.appendChild( el( 'blockquote', null, s.text ) );
			ol.appendChild( li );
		} );
		return ol;
	}

	function renderAnswer( data ) {
		$answer.empty();
		var gen = data.generation, root = $answer[ 0 ];
		var note = { generation_disabled: 'wikitimemachine-gen-disabled', model_unavailable: 'wikitimemachine-gen-unavailable',
			model_output_rejected: 'wikitimemachine-gen-rejected' }[ gen.status ];
		if ( note ) {
			// eslint-disable-next-line mediawiki/msg-doc
			root.appendChild( el( 'p', 'wtm-note', mw.msg( note ) ) );
		}
		var cols = el( 'div', 'wtm-sides' );
		[ [ 'before', 'wikitimemachine-side-before' ], [ 'after', 'wikitimemachine-side-after' ] ].forEach( function ( pair ) {
			var side = pair[ 0 ], snap = data[ side ], card = el( 'article', 'wtm-side' );
			// eslint-disable-next-line mediawiki/msg-doc
			card.appendChild( el( 'h3', null, mw.msg( pair[ 1 ] ) + ' · ' + fmt( snap.requested_at ) ) );
			if ( snap.revision_id ) {
				var lp = el( 'p', 'wtm-resolved' );
				lp.appendChild( link( snap.url, mw.msg( 'wikitimemachine-resolved', snap.revision_id, fmt( snap.source_timestamp ) ) ) );
				card.appendChild( lp );
			}
			var sideInfo = data.sides[ side ];
			if ( sideInfo.status !== 'evidence' ) {
				var key = sideInfo.status === 'insufficient_evidence' ? 'wikitimemachine-insufficient' : 'wikitimemachine-status-' + sideInfo.status;
				// eslint-disable-next-line mediawiki/msg-doc
				card.appendChild( el( 'p', 'wtm-empty', mw.message( key ).exists() ? mw.msg( key ) : sideInfo.message ) );
			} else {
				if ( gen.status === 'ok' ) {
					var ans = gen.answer[ side ];
					card.appendChild( el( 'h4', null, mw.msg( 'wikitimemachine-answer-heading' ) ) );
					if ( ans.status === 'insufficient_evidence' ) {
						card.appendChild( el( 'p', 'wtm-empty', mw.msg( 'wikitimemachine-insufficient' ) ) );
					} else {
						var ul = el( 'ul', 'wtm-statements' );
						ans.statements.forEach( function ( st ) {
							var li = el( 'li', null, st.text );
							li.appendChild( citationList( st.citations ) );
							ul.appendChild( li );
						} );
						card.appendChild( ul );
					}
				}
				card.appendChild( el( 'h4', null, mw.msg( 'wikitimemachine-evidence-heading' ) ) );
				card.appendChild( evidenceList( data.evidence[ side ] ) );
			}
			cols.appendChild( card );
		} );
		if ( gen.status === 'ok' ) {
			var ch = el( 'div', 'wtm-change' );
			ch.appendChild( el( 'h4', null, mw.msg( 'wikitimemachine-change-heading' ) ) );
			if ( gen.answer.change ) {
				var p = el( 'p', null, gen.answer.change.text );
				p.appendChild( citationList( gen.answer.change.citations ) );
				ch.appendChild( p );
			} else {
				ch.appendChild( el( 'p', 'wtm-empty', mw.msg( 'wikitimemachine-no-support' ) ) );
			}
			root.appendChild( ch );
		}
		root.appendChild( cols );
	}

	function ask() {
		var q = $question.val().trim();
		if ( q.length < 3 || !validate() ) {
			return;
		}
		syncUrl();
		$answer.empty().append( el( 'p', 'wtm-status', mw.msg( 'wikitimemachine-loading' ) ) );
		$askBtn.prop( 'disabled', true );
		// Separate (longer) timeout from snapshot/diff requests.
		api.get( {
			action: 'wtmask', formatversion: 2, wiki: wikiOf(), page: $article.val(),
			before: toUtc( $before.val() ), after: toUtc( $after.val() ), question: q
		}, { timeout: 180000 } ).then( function ( res ) {
			renderAnswer( res.wtmask );
		}, function ( code, result ) {
			$answer.empty().append( el( 'p', 'wtm-empty', mw.msg( 'wikitimemachine-compare-error', errorText( code, result ) ) ) );
		} ).always( function () {
			validate();
		} );
	}

	function renderExamples() {
		var box = document.getElementById( 'wtm-examples' );
		( cfg.examples || [] ).forEach( function ( ex ) {
			var b = el( 'button', 'cdx-button wtm-example', ex.label );
			b.type = 'button';
			b.addEventListener( 'click', function () {
				$article.find( 'option' ).each( function () {
					this.selected = this.value === String( ex.page ) && $( this ).attr( 'data-wiki' ) === ( ex.wiki || 'enwiki' );
				} );
				$before.val( fromUtc( ex.before ) );
				$after.val( fromUtc( ex.after ) );
				$question.val( ex.question || '' );
				state.question = ex.question;
				state.autoAsk = false;
				compare();
			} );
			box.appendChild( b );
		} );
	}

	$( function () {
		$form = $( '#wtm-form' );
		$article = $( '#wtm-article' );
		$before = $( '#wtm-before' );
		$after = $( '#wtm-after' );
		$compareBtn = $( '#wtm-compare-button' );
		$validation = $( '#wtm-validation' );
		$windows = $( '#wtm-windows' );
		$status = $( '#wtm-status' );
		$sides = $( '#wtm-sides' );
		$diff = $( '#wtm-diff' );
		$ask = $( '#wtm-ask' );
		$question = $( '#wtm-question' );
		$askBtn = $( '#wtm-ask-button' );
		$answer = $( '#wtm-answer' );

		$form.on( 'submit', function ( e ) {
			e.preventDefault();
			compare();
		} );
		$article.add( $before ).add( $after ).on( 'change input', validate );
		$askBtn.on( 'click', ask );
		$question.on( 'keydown', function ( e ) {
			if ( e.key === 'Enter' ) {
				e.preventDefault();
				ask();
			}
		} );
		renderExamples();
		validate();
		if ( state.page && state.before && state.after ) {
			state.autoAsk = !!state.question;
			compare();
		}
	} );
}() );
