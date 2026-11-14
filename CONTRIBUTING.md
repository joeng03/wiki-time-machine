# Contributing WikiTimeMachine to MediaWiki

WikiTimeMachine is a proposed MediaWiki extension. This repository is currently
an independent prototype, not an accepted Wikimedia project or a deployment on
Wikipedia. No upstream submission or acceptance is claimed.

## Intended destination

Keep the feature in an extension repository rather than adding it to MediaWiki
core. The extension contains the special page, API integration, Python retrieval
service, schema, and tests. The root Docker setup is a reproducible demonstration
environment.

MediaWiki uses Gerrit for code review. Forking the GitHub mirror of MediaWiki core
and opening a GitHub pull request is not the submission path for this extension.
If Wikimedia hosting is agreed, request a dedicated extension repository through
the official new-repository process, then submit changes there for Gerrit review.
The repository name and acceptance remain subject to Wikimedia's process.

## Next steps toward an upstream submission

1. Present the feature and architecture to MediaWiki developers for feedback on
   scope, overlap with existing extensions, and willingness to review and maintain it.
2. Prepare an extension package that installs independently of this demo checkout.
   Document the Python service, MariaDB version requirements, model configuration,
   data ingestion, permissions, and operational costs explicitly.
3. Check compatibility with the upstream development version and run the applicable
   MediaWiki coding, localisation, and automated test checks. Existing README test
   results describe the prototype's pinned environment, not upstream certification.
4. Create the public extension documentation page and request Wikimedia repository
   hosting if appropriate. The extension manifest's documentation URL is an intended
   location, not evidence that the page or project has been accepted.
5. Submit a focused initial change to the agreed extension repository in Gerrit,
   with reproduction instructions and test evidence; address reviewer feedback.

## Deployment on Wikipedia

Hosting or merging extension code does not deploy it on Wikimedia wikis. Deployment
requires a separate proposal and the applicable community, security, performance,
and operations reviews. The external retrieval service and model dependencies need
an explicit operational plan before we can claim deployment readiness.

## Official process

- [Developing and publishing extensions](https://www.mediawiki.org/wiki/Manual:Developing_extensions)
- [How Gerrit works](https://www.mediawiki.org/wiki/Gerrit/How_Gerrit_works)
- [Requesting a Gerrit repository](https://www.mediawiki.org/wiki/Gerrit/New_repositories)
- [Writing an extension for Wikimedia deployment](https://www.mediawiki.org/wiki/Writing_an_extension_for_deployment)
