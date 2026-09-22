# Changelog

## [Unreleased]

### Added

- Codex-native picture-book editorial workflow and baseline slots.
- Feishu authoritative knowledge synchronization and retrieval.
- Craft benchmark, project red lines, Wiki lint, and optional Lexile gates.
- Gated illustration workflow with structured prompt payloads.
- Self-contained HTML preview and illustration asset registry.
- Workspace-first Feishu config discovery, config status, and read-only authority CLI.
- Portable Feishu config discovery for project subdirectories, user directories, and shared plugin distribution.

### Changed

- Split WorkBuddy reference material out of the Codex plugin runtime.
- Stage workflows now represent approval and revision as exclusive outcomes.
- Revision rounds create new runs and rerun preflight, collision, and QA checks.
- v1 run manifests require explicit migration.

### Fixed

- Knowledge loading now rejects page revisions behind the remote index, marks page-ahead evidence as `index_synced=false`, and prevents unsynced reads from replacing the last confirmed cache.
- Terminal stage-run validation now evaluates run-level status and outcome without shadowing by stage fields.
