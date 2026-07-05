# Change Log

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

## [In Development] - Unreleased

## [0.0.1] - 2026-07-05

### Added

- Initial version, forked from the AA example plugin app.
- `Acl`/`AclCharacterEntry`/`AclCorporationEntry`/`AclAllianceEntry` models to
  store ESI Access Lists and their membership.
- `Owner` model plus an "Add / Refresh Character" flow to link a manager
  character's ESI token for syncing.
- Hourly Celery sync task (`acl_checker_setup` management command) pulling
  Access List listings and detail from ESI.
- Flagged Characters page showing characters granted access on a tracked ACL
  who have no owned, registered character in Auth.
