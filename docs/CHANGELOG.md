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

- KB-AI 同步改为内容权威：候选正文与当前页正文比较时只忽略纯排版标记，正文变化即条件覆盖目标页；成功覆盖人工编辑时在报告 `overwritten_human_edits` 中披露逻辑键。退役人工优先三方合并、冲突队列读写与 `queued`/`third_party_edits` 报告字段；`source_edit_times` 作为可选兼容字段接入索引与页尾；`needs_review` 改为可重试的安全状态。
- Split WorkBuddy reference material out of the Codex plugin runtime.
- Stage workflows now represent approval and revision as exclusive outcomes.
- Revision rounds create new runs and rerun preflight, collision, and QA checks.
- v1 run manifests require explicit migration.

### Fixed

- Knowledge loading now rejects page revisions behind the remote index, marks page-ahead evidence as `index_synced=false`, and prevents unsynced reads from replacing the last confirmed cache.
- Terminal stage-run validation now evaluates run-level status and outcome without shadowing by stage fields.
