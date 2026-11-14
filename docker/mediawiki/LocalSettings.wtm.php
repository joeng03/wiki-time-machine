<?php
// Included from LocalSettings.php by scripts/setup-mediawiki.sh.
// Local development settings for the WikiTimeMachine hackathon extension.
wfLoadExtension( 'WikiTimeMachine' );

// Branding for this demo wiki; installing the extension does not change other wikis' logos.
$wtmBrandPath = $wgScriptPath . '/extensions/WikiTimeMachine/resources';
$wgLogos = [
	'1x' => $wtmBrandPath . '/logo.svg',
	'icon' => $wtmBrandPath . '/logo.svg',
	'wordmark' => [ 'src' => $wtmBrandPath . '/wordmark.svg', 'width' => 124, 'height' => 32 ],
];
$wgFavicon = $wtmBrandPath . '/logo.svg';

// Internal URL of the Python retrieval service (not exposed to browsers).
$wgWikiTimeMachineServiceUrl = 'http://service:8000';
// Snapshot/diff requests and model-backed /ask requests have separate timeouts.
$wgWikiTimeMachineServiceTimeout = 15;
$wgWikiTimeMachineAskTimeout = 150;

// Read-only demo wiki.
$wgEnableUploads = false;
$wgGroupPermissions['*']['createaccount'] = false;
$wgGroupPermissions['*']['edit'] = false;
$wgShowExceptionDetails = true;

// Demo shortcuts on the special page (verified against the shipped real-data fixtures).
$wgWikiTimeMachineExamples = [
	[ 'label' => 'Olkiluoto 3: Dec 2021 → Apr 2023', 'page' => 1977274, 'before' => '2021-12-15', 'after' => '2023-04-20',
		'question' => 'How did the article describe the status of Olkiluoto 3 at these two dates?' ],
	[ 'label' => 'Finland–NATO: application → accession', 'page' => 52169647, 'before' => '2022-05-17T12:00:00Z',
		'after' => '2023-04-04T13:59:59Z', 'question' => 'Was Finland a member of NATO?' ],
	[ 'label' => 'Oodi library: before → after opening', 'page' => 45223395, 'before' => '2018-11-15', 'after' => '2019-01-13T02:00:00Z',
		'question' => 'When did the library open?' ],
];
// Organizer dataset (MariaDB "M", Finnish Wikipedia full history) example.
$wgWikiTimeMachineExamples[] = [ 'label' => 'Erik Edner (fiwiki, organizer M dataset)', 'wiki' => 'fiwiki', 'page' => 186,
	'before' => '2008-01-01', 'after' => '2026-09-30', 'question' => 'Kuka oli Erik Edner? / Who was Erik Edner?' ];
// Bitemporal claims examples (Prime Minister / President of Finland handovers).
$wgWikiTimeMachineExamples[] = [ 'label' => 'PM of Finland: Marin → Orpo (bitemporal)', 'page' => 231789,
	'before' => '2023-06-15', 'after' => '2023-06-25', 'validat' => '2023-06-22',
	'question' => 'Who was the prime minister according to each version?' ];
$wgWikiTimeMachineExamples[] = [ 'label' => 'President of Finland: Niinistö → Stubb (bitemporal)', 'page' => 196964,
	'before' => '2024-02-20', 'after' => '2024-03-05', 'validat' => '2024-03-05',
	'question' => 'Who was the president according to each version?' ];
