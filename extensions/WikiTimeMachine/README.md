# Extension:WikiTimeMachine

Read-only MediaWiki extension: `Special:WikiTimeMachine` plus API modules `wtmarticles`, `wtmsnapshot`,
`wtmcompare`, `wtmask` (read right required; server-side calls to the internal retrieval service).

```php
wfLoadExtension( 'WikiTimeMachine' );
$wgWikiTimeMachineServiceUrl = 'http://service:8000';
```

The Python service (`service/`), app schema (`sql/`), fixtures and tests live in this package.
See the repository README for setup, the data model, measured results and limitations.
