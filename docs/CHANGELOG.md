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
- `text_quality_prefilter`: every active red line and the five per-page text-quality dimensions are pre-screened by Jev, with Chinese calibration samples and a threshold calibration tool.
- `text_quality_prefilter` now has its operator entry point: `skills/pre_output-baseline/scripts/screening_cli.py` screens a draft's page table against the authority red-line catalog, writes the escalation package for the plain LLM, and reports the ratios, `catalog_gap`, `blocked_records` and warnings instead of leaving the orchestration reachable only from tests.

### Changed

- Split WorkBuddy reference material out of the Codex plugin runtime.
- Stage workflows now represent approval and revision as exclusive outcomes.
- Revision rounds create new runs and rerun preflight, collision, and QA checks.
- v1 run manifests require explicit migration.
- The entry workflow now opens with an `## Execution Choice Gate` before intent classification on every top-level request.

### Fixed

- The machine-readable contract now states that a `project_authority` finding blocks the confirmation gate at any severity (`any_finding_blocks_confirmation`), matching the runtime, where promotion forces `severity="FAIL"` and a later judgment can no longer soften it.
- Knowledge loading now rejects page revisions behind the remote index, marks page-ahead evidence as `index_synced=false`, and prevents unsynced reads from replacing the last confirmed cache.
- Terminal stage-run validation now evaluates run-level status and outcome without shadowing by stage fields.
- `knowledge_relevance` now reuses a stored terminal result only when it records the request it answered, so an edited authority page is never routed on the previous edit's verdicts; the record that keeps a batch out of the screen is reported as `blocked_records`, and a failed batch's pending record is dropped under the operation lease instead of after it.
- A screening batch keeps its record instead of paying again when the file on disk exists but cannot be read as one: a `result.json` that fails to parse or parses to a non-object is now reported as `unreadable_result` rather than treated as no record, and a pending record whose `attempt_status` is neither a waiting call nor a settled failure is reported as `unreadable_pending`. A result that names no request at all is reported as unreadable rather than stale.
- A settled operation clears the run-level `pending_call` in `decision-context.json` only when no other operation is still waiting, so a second batch's ambiguous call stays named for every reader of the context.
- A semantic judgment can no longer downgrade a literal project red-line hit to PASS: proxy hits and confirmed violations are now separate sources, and only a confirmed final review produces a blocking finding.
