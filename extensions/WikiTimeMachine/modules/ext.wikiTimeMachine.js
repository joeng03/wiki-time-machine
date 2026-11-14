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
		lastCompare = null, comparisonRequest = 0, claimsRequest = 0, askRequest = 0,
		timelineKey = '', timelineRequest = 0, timelineData = null, timelineIndex = 0;

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

	// ---------------------------------------------------------------- timeline and claim evolution

	function resetComparison() {
		comparisonRequest++;
		claimsRequest++;
		askRequest++;
		lastCompare = null;
		$sides.empty();
		$diff.empty();
		$answer.empty();
		$status.empty();
		$ask.prop( 'hidden', true );
		$( '#wtm-claims' ).prop( 'hidden', true );
	}

	function selectTimelineRevision( index ) {
		if ( !timelineData || !timelineData.states.length ) {
			return;
		}
		timelineIndex = Math.max( 0, Math.min( index, timelineData.states.length - 1 ) );
		var s = timelineData.states[ timelineIndex ], root = document.getElementById( 'wtm-timeline-selected' );
		$( root ).empty();
		$( '#wtm-timeline-slider' ).val( timelineIndex ).attr( 'aria-valuetext', fmt( s.source_timestamp ) + ', oldid=' + s.revision_id );
		$( '#wtm-timeline-prev' ).prop( 'disabled', timelineIndex === 0 );
		$( '#wtm-timeline-next' ).prop( 'disabled', timelineIndex === timelineData.states.length - 1 );
		$( '#wtm-timeline-track button' ).removeClass( 'wtm-tick-selected' ).eq( timelineIndex ).addClass( 'wtm-tick-selected' );
		root.appendChild( el( 'p', 'wtm-hint', mw.msg( 'wikitimemachine-timeline-position', timelineIndex + 1, timelineData.states.length ) ) );
		root.appendChild( link( s.url, mw.msg( 'wikitimemachine-resolved', s.revision_id, fmt( s.source_timestamp ) ) ) );
		root.appendChild( el( 'p', 'wtm-hint', mw.msg( 'wikitimemachine-timeline-interval', fmt( s.start ), fmt( s.end ) ) ) );
		if ( !s.content_available ) {
			root.appendChild( el( 'p', 'wtm-empty', mw.msg( 'wikitimemachine-timeline-unavailable' ) ) );
		} else if ( s.claims.length ) {
			var sources = el( 'details' ), list = el( 'ul', 'wtm-claim-list' );
			sources.appendChild( el( 'summary', null, mw.msg( 'wikitimemachine-timeline-selected-claims' ) ) );
			s.claims.forEach( function ( c ) { list.appendChild( claimItem( c ) ); } );
			sources.appendChild( list );
			root.appendChild( sources );
		} else if ( selectedArticle().claim_rows ) {
			root.appendChild( el( 'p', 'wtm-empty', mw.msg( 'wikitimemachine-timeline-no-claims' ) ) );
		}
	}

	function useTimelineRevision( side ) {
		if ( !timelineData || !timelineData.states.length ) { return; }
		var s = timelineData.states[ timelineIndex ];
		( side === 'before' ? $before : $after ).val( fromUtc( s.start ) );
		state.autoAsk = false;
		resetComparison();
		if ( validate() ) { compare(); }
	}

	function renderEvolution( data ) {
		var root = document.getElementById( 'wtm-evolution-events' ), events = 0;
		$( root ).empty();
		$( '#wtm-evolution' ).prop( 'hidden', !selectedArticle().claim_rows );
		data.states.forEach( function ( s, index ) {
			var previous = index ? data.states[ index - 1 ] : null,
				contiguous = previous && previous.end === s.start,
				unavailable = !s.content_available || ( previous && !previous.content_available ),
				oldClaims = contiguous && !unavailable ? previous.claims : [],
				added = s.claims.filter( function ( c ) { return !oldClaims.some( function ( old ) { return old.claim_id === c.claim_id; } ); } ),
				removed = oldClaims.filter( function ( c ) { return !s.claims.some( function ( current ) { return current.claim_id === c.claim_id; } ); } );
			if ( !added.length && !removed.length && contiguous && !unavailable ) { return; }
			if ( !s.claims.length && !oldClaims.length && !previous && !unavailable ) { return; }
			events++;
			var card = el( 'article', 'wtm-evolution-event' ), title = el( 'h3' );
			title.appendChild( link( s.url, fmt( s.start ) + ' · oldid=' + s.revision_id ) );
			card.appendChild( title );
			if ( unavailable ) {
				card.appendChild( el( 'p', 'wtm-note', mw.msg( 'wikitimemachine-evolution-unavailable' ) ) );
			} else if ( !contiguous ) {
				card.appendChild( el( 'p', 'wtm-note', mw.msg( previous ? 'wikitimemachine-evolution-boundary' : 'wikitimemachine-evolution-initial' ) ) );
			}
			[ [ contiguous && !unavailable ? 'wikitimemachine-evolution-added' : 'wikitimemachine-timeline-selected-claims', added ],
				[ 'wikitimemachine-evolution-removed', removed ] ].forEach( function ( group ) {
				if ( !group[ 1 ].length ) { return; }
				card.appendChild( el( 'h4', null, mw.msg( group[ 0 ] ) ) );
				group[ 1 ].forEach( function ( c ) {
					card.appendChild( el( 'p', null, c.holder + ' · ' + c.display ) );
				} );
				var details = el( 'details' ), list = el( 'ul', 'wtm-claim-list' );
				details.appendChild( el( 'summary', null, mw.msg( 'wikitimemachine-evolution-sources' ) ) );
				group[ 1 ].forEach( function ( c ) { list.appendChild( claimItem( c ) ); } );
				details.appendChild( list );
				card.appendChild( details );
			} );
			if ( !s.claims.length ) {
				card.appendChild( el( 'p', 'wtm-empty', mw.msg( s.content_available ? 'wikitimemachine-timeline-no-claims' : 'wikitimemachine-timeline-unavailable' ) ) );
			}
			if ( contiguous ) {
				var button = el( 'button', 'cdx-button', mw.msg( 'wikitimemachine-evolution-compare' ) );
				button.type = 'button';
				button.addEventListener( 'click', function () {
					$before.val( fromUtc( previous.start ) );
					$after.val( fromUtc( s.start ) );
					state.autoAsk = false;
					compare();
				} );
				card.appendChild( button );
			}
			root.appendChild( card );
		} );
		if ( !events ) { root.appendChild( el( 'p', 'wtm-empty', mw.msg( 'wikitimemachine-evolution-empty' ) ) ); }
	}

	function loadTimeline() {
		var art = selectedArticle(), key = art ? art.wiki_id + ':' + art.page_id : '';
		if ( art && key === timelineKey ) { return; }
		timelineKey = key;
		timelineData = null;
		var request = ++timelineRequest;
		$( '#wtm-timeline' ).prop( 'hidden', !art );
		$( '#wtm-timeline-controls, #wtm-evolution' ).prop( 'hidden', true );
		$( '#wtm-timeline-track, #wtm-timeline-selected, #wtm-evolution-events' ).empty();
		$( '#wtm-evolution' ).prop( 'open', false );
		if ( !art ) { return; }
		$( '#wtm-timeline-status' ).text( mw.msg( 'wikitimemachine-loading' ) );
		var start = art.windows[ 0 ].start, end = art.windows[ art.windows.length - 1 ].end;
		api.get( { action: 'wtmtimeline', formatversion: 2, wiki: art.wiki_id, page: art.page_id, start: start, end: end },
			{ timeout: 30000 } ).then( function ( res ) {
			if ( request !== timelineRequest ) { return; }
			var data = res.wtmtimeline, track = document.getElementById( 'wtm-timeline-track' ),
				min = Date.parse( start ), duration = Date.parse( end ) - min;
			timelineData = data;
			$( '#wtm-timeline-status' ).text( data.truncated ? mw.msg( 'wikitimemachine-timeline-truncated', data.limit ) :
				( data.states.length ? '' : mw.msg( 'wikitimemachine-timeline-empty' ) ) );
			data.windows.forEach( function ( w ) {
				var band = el( 'span', 'wtm-timeline-coverage' );
				band.style.left = ( Date.parse( w.start ) - min ) / duration * 100 + '%';
				band.style.width = ( Date.parse( w.end ) - Date.parse( w.start ) ) / duration * 100 + '%';
				band.title = fmt( w.start ) + ' – ' + fmt( w.end );
				band.setAttribute( 'aria-hidden', 'true' );
				track.appendChild( band );
			} );
			var index = 0, before = Date.parse( toUtc( $before.val() ) );
			data.states.forEach( function ( s, i ) {
				var tick = el( 'button', 'wtm-timeline-tick' );
				tick.type = 'button';
				tick.tabIndex = -1; // The labelled native slider exposes every revision to keyboard users.
				tick.title = fmt( s.source_timestamp ) + ' · oldid=' + s.revision_id;
				tick.setAttribute( 'aria-label', tick.title );
				tick.style.left = ( Date.parse( s.start ) - min ) / duration * 100 + '%';
				tick.addEventListener( 'click', function () { selectTimelineRevision( i ); } );
				track.appendChild( tick );
				if ( Date.parse( s.start ) <= before && before < Date.parse( s.end ) ) { index = i; }
			} );
			$( '#wtm-timeline-slider' ).attr( 'max', Math.max( 0, data.states.length - 1 ) );
			$( '#wtm-timeline-controls' ).prop( 'hidden', !data.states.length );
			selectTimelineRevision( index );
			renderEvolution( data );
		}, function ( code, result ) {
			if ( request !== timelineRequest ) { return; }
			timelineKey = ''; // Allow a retry after a temporary service failure.
			$( '#wtm-timeline-status' ).text( mw.msg( 'wikitimemachine-timeline-error', errorText( code, result ) ) );
		} );
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
		resetComparison();
		loadTimeline();
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
		var request = comparisonRequest;
		api.get( {
			action: 'wtmcompare', formatversion: 2, wiki: wikiOf(),
			page: $article.val(), before: toUtc( $before.val() ), after: toUtc( $after.val() )
		}, { timeout: 30000 } ).then( function ( res ) {
			if ( request !== comparisonRequest ) { return; }
			var data = res.wtmcompare;
			lastCompare = data;
			$status.text( '' );
			$sides.append(
				renderSide( mw.msg( 'wikitimemachine-side-before' ), data.before, data.date_only_end_of_day.before ),
				renderSide( mw.msg( 'wikitimemachine-side-after' ), data.after, data.date_only_end_of_day.after )
			);
			renderDiff( data );
			$ask.prop( 'hidden', false );
			maybeShowClaims();
			validate();
			if ( state.question && $question.val() === state.question && state.autoAsk ) {
				state.autoAsk = false;
				ask();
			}
		}, function ( code, result ) {
			if ( request !== comparisonRequest ) { return; }
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
		if ( q.length < 3 || !validate() || !lastCompare ) {
			return;
		}
		syncUrl();
		$answer.empty().append( el( 'p', 'wtm-status', mw.msg( 'wikitimemachine-loading' ) ) );
		$askBtn.prop( 'disabled', true );
		var request = ++askRequest;
		// Separate (longer) timeout from snapshot/diff requests.
		api.get( {
			action: 'wtmask', formatversion: 2, wiki: wikiOf(), page: $article.val(),
			before: toUtc( $before.val() ), after: toUtc( $after.val() ), question: q
		}, { timeout: 180000 } ).then( function ( res ) {
			if ( request !== askRequest || q !== $question.val().trim() ) { return; }
			renderAnswer( res.wtmask );
		}, function ( code, result ) {
			if ( request !== askRequest || q !== $question.val().trim() ) { return; }
			$answer.empty().append( el( 'p', 'wtm-empty', mw.msg( 'wikitimemachine-compare-error', errorText( code, result ) ) ) );
		} ).always( function () {
			if ( request === askRequest ) { validate(); }
		} );
	}

	// ---------------------------------------------------------------- bitemporal claims

	function claimItem( c ) {
		var li = el( 'li', 'wtm-claim' );
		li.appendChild( el( 'strong', null, c.holder ) );
		// eslint-disable-next-line mediawiki/msg-doc
		li.appendChild( document.createTextNode( ' — ' + c.display + ' (' + mw.msg( 'wikitimemachine-claims-status-' + c.end_status ) + ')' ) );
		c.sources.forEach( function ( s ) {
			var src = el( 'div', 'wtm-claim-src' );
			var label = s.extractor.indexOf( 'llm:' ) === 0 ? mw.msg( 'wikitimemachine-claims-extractor-llm' ) :
				mw.msg( 'wikitimemachine-claims-extractor-infobox' );
			src.appendChild( el( 'span', 'wtm-dist', label + ' · ' ) );
			src.appendChild( link( s.url, 'oldid=' + s.revision_id ) );
			src.appendChild( el( 'blockquote', null, s.excerpt ) );
			li.appendChild( src );
		} );
		return li;
	}

	function renderClaims( data ) {
		var root = document.getElementById( 'wtm-claims-result' );
		$( root ).empty();
		var cols = el( 'div', 'wtm-sides' );
		[ 'before', 'after' ].forEach( function ( side ) {
			var s = data.sides[ side ], card = el( 'article', 'wtm-side' );
			card.appendChild( el( 'h3', null, mw.msg( 'wikitimemachine-claims-asof', fmt( s.snapshot.requested_at ) ) ) );
			if ( s.snapshot.revision_id ) {
				var p = el( 'p', 'wtm-resolved' );
				p.appendChild( link( s.snapshot.url, mw.msg( 'wikitimemachine-resolved', s.snapshot.revision_id, fmt( s.snapshot.source_timestamp ) ) ) );
				card.appendChild( p );
			}
			if ( s.claims === null ) {
				card.appendChild( el( 'p', 'wtm-empty', mw.msg( 'wikitimemachine-claims-unavailable', s.snapshot.message || s.snapshot.status ) ) );
			} else if ( !s.claims.length ) {
				card.appendChild( el( 'p', 'wtm-empty', mw.msg( 'wikitimemachine-claims-none' ) ) );
			} else {
				var ul = el( 'ul', 'wtm-claim-list' );
				s.claims.forEach( function ( c ) {
					ul.appendChild( claimItem( c ) );
				} );
				card.appendChild( ul );
			}
			cols.appendChild( card );
		} );
		root.appendChild( cols );
		if ( data.history && data.history.length ) {
			root.appendChild( el( 'h3', null, mw.msg( 'wikitimemachine-claims-history' ) ) );
			var table = el( 'table', 'wikitable wtm-claim-history' ), thead = el( 'tr' );
			mw.msg( 'wikitimemachine-claims-history-cols' ).split( '|' ).forEach( function ( h ) {
				thead.appendChild( el( 'th', null, h ) );
			} );
			table.appendChild( thead );
			data.history.forEach( function ( h ) {
				var tr = el( 'tr' );
				[ fmt( h.asserted_from ), fmt( h.asserted_until ), h.holder, h.display ].forEach( function ( v ) {
					tr.appendChild( el( 'td', null, v ) );
				} );
				table.appendChild( tr );
			} );
			root.appendChild( table );
		}
		var det = el( 'details', 'wtm-sql' );
		det.appendChild( el( 'summary', null, mw.msg( 'wikitimemachine-claims-sql' ) ) );
		det.appendChild( el( 'pre', null, data.sql ) );
		root.appendChild( det );
	}

	function checkClaims() {
		var v = $( '#wtm-validat' ).val();
		if ( !/^\d{4}-\d{2}-\d{2}$/.test( v ) || !validate() ) {
			return;
		}
		var url = new URL( window.location.href );
		url.searchParams.set( 'validat', v );
		window.history.replaceState( null, '', url.toString() );
		$( '#wtm-claims-result' ).empty().append( el( 'p', 'wtm-status', mw.msg( 'wikitimemachine-loading' ) ) );
		var request = ++claimsRequest, comparison = lastCompare;
		api.get( {
			action: 'wtmclaims', formatversion: 2, wiki: wikiOf(), page: $article.val(),
			before: toUtc( $before.val() ), after: toUtc( $after.val() ), validat: v
		}, { timeout: 30000 } ).then( function ( res ) {
			if ( request !== claimsRequest || comparison !== lastCompare ) { return; }
			renderClaims( res.wtmclaims );
		}, function ( code, result ) {
			if ( request !== claimsRequest || comparison !== lastCompare ) { return; }
			$( '#wtm-claims-result' ).empty().append( el( 'p', 'wtm-empty', mw.msg( 'wikitimemachine-compare-error', errorText( code, result ) ) ) );
		} );
	}

	function maybeShowClaims() {
		var art = selectedArticle(), show = !!( art && art.claim_rows > 0 && lastCompare );
		$( '#wtm-claims' ).prop( 'hidden', !show );
		if ( show ) {
			if ( !$( '#wtm-validat' ).val() ) {
				$( '#wtm-validat' ).val( toUtc( $after.val() ).slice( 0, 10 ) );
			}
			checkClaims();
		}
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
				$( '#wtm-validat' ).val( ex.validat || '' );
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
		$article.add( $before ).add( $after ).on( 'change input', function () {
			resetComparison();
			validate();
			loadTimeline();
		} );
		$( '#wtm-timeline-slider' ).on( 'input change', function () { selectTimelineRevision( Number( this.value ) ); } );
		$( '#wtm-timeline-prev' ).on( 'click', function () { selectTimelineRevision( timelineIndex - 1 ); } );
		$( '#wtm-timeline-next' ).on( 'click', function () { selectTimelineRevision( timelineIndex + 1 ); } );
		$( '#wtm-timeline-before' ).on( 'click', function () { useTimelineRevision( 'before' ); } );
		$( '#wtm-timeline-after' ).on( 'click', function () { useTimelineRevision( 'after' ); } );
		$askBtn.on( 'click', ask );
		$( '#wtm-claims-button' ).on( 'click', checkClaims );
		$question.on( 'keydown', function ( e ) {
			if ( e.key === 'Enter' ) {
				e.preventDefault();
				ask();
			}
		} );
		renderExamples();
		validate();
		loadTimeline();
		if ( state.page && state.before && state.after ) {
			state.autoAsk = !!state.question;
			compare();
		}
	} );
}() );
