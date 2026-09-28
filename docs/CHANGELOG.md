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
- Side-by-side comparison report for the two execution paths: case-identity comparability gate, Jev traces merged with manually entered plain-LLM usage, per-operation calibration advice, and an export that only happens on an explicit request.

### Changed

- Split WorkBuddy reference material out of the Codex plugin runtime.
- Stage workflows now represent approval and revision as exclusive outcomes.
- Revision rounds create new runs and rerun preflight, collision, and QA checks.
- v1 run manifests require explicit migration.
- The entry workflow now opens with an `## Execution Choice Gate` before intent classification on every top-level request.

### Fixed

- The machine-readable contract now states that a `project_authority` finding blocks the confirmation gate at any severity (`any_finding_blocks_confirmation`), matching the runtime, where promotion forces `severity="FAIL"` and a later judgment can no longer soften it.
- 对比报告的 Jev 列不再恒为 0：共享 runner 的 `verdicts=` 钩子把两个 operation 真正判定的 `screened_clear_count` / `escalated_count` 写进 trace（此前 `trace_for` 写死 0，升级率与校准建议因此结构性失效）。校准建议改为逐 operation 读取该 operation 自己的计数，本次没有测量的 operation 不再给出建议；`knowledge_items_entered` / `quality_items_entered` 各自取自本 operation 的升级项；运行目录里出现多次运行（trace 的 `run_id` 不止一个）时判 `comparable: false` 并列出 run_id，而不是把两次运行静默相加；声明 case id 无法从运行目录核验时补记原因并把 trace 数写进报告。
- 命令行凭据闸改为按旗标形状匹配：`--openai-api-key`、`--access-key`、`--password` 等与 `--api-key` 同样被拒，因为它们都会被 argparse 原样回显到终端；同时只判旗标名，路径里出现 `key` 不受影响。
- Knowledge loading now rejects page revisions behind the remote index, marks page-ahead evidence as `index_synced=false`, and prevents unsynced reads from replacing the last confirmed cache.
- Terminal stage-run validation now evaluates run-level status and outcome without shadowing by stage fields.
- `knowledge_relevance` now reuses a stored terminal result only when it records the request it answered, so an edited authority page is never routed on the previous edit's verdicts; the record that keeps a batch out of the screen is reported as `blocked_records`, and a failed batch's pending record is dropped under the operation lease instead of after it.
- A screening batch keeps its record instead of paying again when the file on disk exists but cannot be read as one: a `result.json` that fails to parse or parses to a non-object is now reported as `unreadable_result` rather than treated as no record, and a pending record whose `attempt_status` is neither a waiting call nor a settled failure is reported as `unreadable_pending`. A result that names no request at all is reported as unreadable rather than stale.
- A settled operation clears the run-level `pending_call` in `decision-context.json` only when no other operation is still waiting, so a second batch's ambiguous call stays named for every reader of the context.
- A semantic judgment can no longer downgrade a literal project red-line hit to PASS: proxy hits and confirmed violations are now separate sources, and only a confirmed final review produces a blocking finding.
