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
- Jev-assisted decision layer infrastructure: an explicit execution choice gate, `pb-decision-context-v1` propagation, credential waiting and recovery points, a single-holder execution lease, atomic terminal results, decimal cost estimation, and benchmark case alignment.
- `knowledge_relevance`: authority pages are chunked, hard-constraint chunks are marked unfilterable, soft candidates are recalled, and Jev screens those candidates; the reduced evidence bundle feeds the ordinary model context while the unfiltered revision vector stays available to the dependency lock.

### Changed

- Split WorkBuddy reference material out of the Codex plugin runtime.
- Stage workflows now represent approval and revision as exclusive outcomes.
- Revision rounds create new runs and rerun preflight, collision, and QA checks.
- v1 run manifests require explicit migration.
- The entry workflow now opens with an `## Execution Choice Gate` before intent classification on every top-level request.

### Fixed

- Knowledge loading now rejects page revisions behind the remote index, marks page-ahead evidence as `index_synced=false`, and prevents unsynced reads from replacing the last confirmed cache.
- Terminal stage-run validation now evaluates run-level status and outcome without shadowing by stage fields.
- `knowledge_relevance` now reuses a stored terminal result only when it records the request it answered, so an edited authority page is never routed on the previous edit's verdicts; the record that keeps a batch out of the screen is reported as `blocked_records`, and a failed batch's pending record is dropped under the operation lease instead of after it.
