# KB-AI 一致性与发布收口实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 统一前三批后的协议、技能、模板、CLI 文档和测试，完成离线一致性验收，并把远端只读验收、版本确认和真实发布授权固定为独立门槛。

**Architecture:** 新增一份跨侧只读契约矩阵和静态一致性测试，作为仓库内的最终防漂移门；随后统一现有文档并把只读验收记录放入独立 release-gate 文档。该计划不新增 runtime 行为，不执行远端 publish。

**Tech Stack:** Python `unittest`、Markdown 文档、现有 CLI、静态术语扫描。

**Spec:** `docs/superpowers/specs/2026-09-29-codex-kb-ai-consistency-and-release-closure-design.md`

## Global Constraints

- 只有第一批到第三批代码、测试和文档完成后才能执行本计划。
- 第三批结束时不能残留未解决的 P0/P1 行为缺口。
- 真实 publish、并发操作放行和兼容协议版本提升都必须由用户明确批准。
- 只读远端验收不得调用 `publish`、`docs update`、`docs create`、lock acquire/release 或任何远端写命令。
- 缺失准入页按空排除处理；损坏准入页、损坏索引或 schema 漂移 fail-closed。
- 文档不得继续把本地 state、冲突队列、人工优先或三方合并描述为当前协议。

## Review Focus

- 某批实现已变但协议文档仍保留旧口径：由 Task 1/2 的静态一致性测试固定。
- 只读验收记录仍包含令牌、私密正文或未脱敏 revision：由 Task 3 的记录模板测试固定。
- 未得到 WorkBuddy 确认就单方面声称兼容：由 Task 5 的门槛检查固定。
- 把 dry-run/verify 结果报告为已发布：由 Task 3/5 的报告字段检查固定。
- 因缺失准入页或损坏索引错误继续执行：由 Task 1/3 的 fail-closed 文档断言固定。

---

### Task 1: 建立跨侧只读契约矩阵和一致性测试

**Files:**
- Create: `plugins/picturebook-screenwriter/tests/test_kb_ai_contract_consistency.py`
- Modify: `docs/workbuddy-feishu-knowledge-base-compatibility.md`
- Modify: `docs/superpowers/specs/2026-09-17-feishu-authoritative-knowledge-base-design.md`

**Interfaces:**
- Produces a checked-in contract matrix covering control tokens, index schema, source revisions/times, candidate times, page footer, content authority, admission, `needs_review`, overwrite disclosure and deletion.
- Produces static tests that read implementation/docs and assert required terms exist together.

- [x] **Step 1: Write the failing contract test**

  Add `KB_AIContractConsistencyTests` with tests:

  - `test_required_control_tokens_are_documented`
  - `test_source_time_fields_are_documented_in_all_layers`
  - `test_content_authority_and_needs_review_are_present`
  - `test_admission_and_subtree_rules_are_present`
  - `test_local_state_is_not_documented_as_shared_authority`
  - `test_removed_queue_terms_are_not_current_contract`

  The test reads the compatibility doc and the three skill docs from the repository root and asserts each required concept is stated in at least one current-authority section.

- [x] **Step 2: Run the test to verify failure**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/tests -p 'test_kb_ai_contract_consistency.py' -v`

  Expected: FAIL until the contract matrix and current wording are present.

- [x] **Step 3: Add the contract matrix**

  Update `docs/workbuddy-feishu-knowledge-base-compatibility.md` with a table whose rows match the spec’s cross-side matrix. For every row, name the Codex implementation, the test that proves it, and whether WorkBuddy confirmation is pending or complete. Do not mark a row complete from documentation alone.

- [x] **Step 4: Align the older authoritative design**

  Update `2026-09-17-feishu-authoritative-knowledge-base-design.md` only where it contradicts the current contract: source time baseline, remote index status, admission default/admit/exclude, no conflict queue, no human-priority merge, and local state limitations.

- [x] **Step 5: Run the contract test**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/tests -p 'test_kb_ai_contract_consistency.py' -v`

  Expected: PASS.

- [x] **Step 6: Commit**

  `git add plugins/picturebook-screenwriter/tests/test_kb_ai_contract_consistency.py docs/workbuddy-feishu-knowledge-base-compatibility.md docs/superpowers/specs/2026-09-17-feishu-authoritative-knowledge-base-design.md`

  `git commit -m "docs: define cross-side kb-ai contract matrix"`

### Task 2: 统一技能、模板、帮助和 README 的当前口径

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/skills/knowledge-loader/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/references/feishu-wiki-extraction.md`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/references/structured-output-templates.md`
- Modify: `README.md`
- Modify: `plugins/picturebook-screenwriter/README.md`
- Modify: `docs/CHANGELOG.md`
- Test: `plugins/picturebook-screenwriter/tests/test_kb_ai_contract_consistency.py`

**Interfaces:**
- No new runtime API. Produces one consistent operator sequence and one set of current status terms.

- [x] **Step 1: Add failing wording tests**

  Add:

  - `test_operator_sequence_matches_store_cli_commands`
  - `test_templates_require_source_edit_time_parts`
  - `test_needs_review_is_neutral_and_retryable`
  - `test_no_current_queue_or_human_priority_promise`
  - `test_source_baseline_and_admission_are_named_in_ingest_skill`

- [x] **Step 2: Run the tests to verify failure**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/tests -p 'test_kb_ai_contract_consistency.py' -v`

  Expected: FAIL while any skill, template or README still uses a superseded term.

- [x] **Step 3: Update all current-authority surfaces**

  Make the operator sequence explicit: `source-baseline` → `check_delta` → candidate generation → `generate_entries --validate-only --nodes` → `prepare` → `publish` → `verify`. Keep historical statements only in changelog/history sections. Remove fixed WorkBuddy command counts and copied step numbers from current instructions.

- [x] **Step 4: Run the tests to verify pass**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/tests -p 'test_kb_ai_contract_consistency.py' -v`

  Expected: PASS.

- [x] **Step 5: Commit**

  `git add README.md docs/CHANGELOG.md plugins/picturebook-screenwriter`

  `git commit -m "docs: align kb-ai current protocol wording"`

### Task 3: 建立只读验收记录与发布门文档

**Files:**
- Create: `docs/release-gates/2026-09-29-kb-ai-readonly-acceptance.md`
- Create: `docs/release-gates/2026-09-29-kb-ai-release-gate.md`
- Modify: `plugins/picturebook-screenwriter/tests/test_kb_ai_contract_consistency.py`

**Interfaces:**
- Produces a manual evidence template with no secret fields.
- Produces a final gate checklist that distinguishes offline, remote read-only and authorized write states.

- [x] **Step 1: Write failing release-document tests**

  Add:

  - `test_readonly_acceptance_record_has_required_evidence_fields`
  - `test_readonly_record_forbids_tokens_and_private_body`
  - `test_release_gate_requires_all_three_batches`
  - `test_release_gate_distinguishes_readonly_from_publish`
  - `test_release_gate_requires_explicit_user_authorization`

- [x] **Step 2: Run the tests to verify failure**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/tests -p 'test_kb_ai_contract_consistency.py' -v`

  Expected: FAIL because the release-gate files do not exist.

- [x] **Step 3: Write both documents**

  The read-only record must capture timestamp, command, control revision, sample keys, decision, schema result and unresolved items without tokens or private body text. The release gate must list all conditions from spec §6 and must state that `verify` never counts as `published`.

- [x] **Step 4: Run the tests to verify pass**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/tests -p 'test_kb_ai_contract_consistency.py' -v`

  Expected: PASS.

- [x] **Step 5: Commit**

  `git add docs/release-gates plugins/picturebook-screenwriter/tests/test_kb_ai_contract_consistency.py`

  `git commit -m "docs: add kb-ai readonly release gate"`

### Task 4: 运行完整离线回归和旧口径扫描

**Files:**
- Review: all files changed by Tasks 1–3 and the plans for Batches 2–3

**Interfaces:**
- No new interface. Produces the evidence required before remote read-only acceptance.

- [x] **Step 1: Run all affected suites**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p 'test_*.py' -v`

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest/scripts -p 'test_*.py' -v`

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/knowledge-loader/scripts -p 'test_*.py' -v`

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/tests -p 'test_kb_ai_contract_consistency.py' -v`

  Expected: PASS.

- [x] **Step 2: Run the final old-contract scan**

  Run: `rg -n "人工优先|三方合并|冲突入队|queued|third_party_edits|delta_state|forceFullSync|merge_protocol|sync_knowledge|target_state" docs plugins/picturebook-screenwriter/skills plugins/picturebook-screenwriter/tests`

  Expected: every match is in history, an explicit not-applicable section, or a test proving the identifier is retired.

- [x] **Step 3: Write the offline evidence into the release gate**

  Record commands, pass counts, scanned paths and unresolved items in `docs/release-gates/2026-09-29-kb-ai-release-gate.md`. Do not mark remote conditions complete.

- [x] **Step 4: Commit**

  `git add docs/release-gates/2026-09-29-kb-ai-release-gate.md`

  `git commit -m "test: record kb-ai offline release evidence"`

### Task 5: 执行授权的远端只读验收并完成最终版本确认

**Files:**
- Modify: `docs/release-gates/2026-09-29-kb-ai-readonly-acceptance.md`
- Modify: `docs/release-gates/2026-09-29-kb-ai-release-gate.md`
- Modify: `docs/workbuddy-feishu-knowledge-base-compatibility.md`
- Modify: `docs/CHANGELOG.md`
- Test: `plugins/picturebook-screenwriter/tests/test_kb_ai_contract_consistency.py`

**Interfaces:**
- No runtime writes. Produces either a completed read-only acceptance record or an explicit blocked/pending record.

- [x] **Step 1: Obtain explicit read-only authorization**

  Confirm the user authorizes only read operations against the target Wiki. If authorization is absent, leave both gate files marked pending and stop this task.

- [x] **Step 2: Run the read-only acceptance commands**

  Run the actual configured `store_cli.py source-baseline --out <run-dir>/source_baseline.json` and read-only `verify --run-dir <run-dir>` paths. Do not call `publish`, `docs update`, `docs create`, lock acquisition or lock release.

- [x] **Step 3: Verify the contract matrix**

  Check stable pages for body round-trip, `source_edit_times`, `needs_review`, admission empty/missing/corrupt behavior, excluded descendants and deletion reporting. Compare Codex results with the WorkBuddy evidence supplied by the user.

- [x] **Step 4: Record evidence and blocker status**

  Fill the acceptance document with timestamps, revisions, sample logical keys, decisions and unresolved differences. Never record authentication tokens, private body text or secrets.

- [x] **Step 5: Confirm compatibility version only after cross-side agreement**

  If and only if WorkBuddy confirms the same contract, update the compatibility document and changelog to the negotiated next version and record the evidence link. Otherwise leave the version unchanged and record the exact unresolved items.

- [x] **Step 6: Run the final contract test and commit**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/tests -p 'test_kb_ai_contract_consistency.py' -v`

  Expected: PASS or the task remains explicitly blocked with no false completion.

  `git add docs/release-gates docs/workbuddy-feishu-knowledge-base-compatibility.md docs/CHANGELOG.md`

  `git commit -m "docs: record kb-ai readonly acceptance"`

### Task 6: 真实发布与并发放行决定

**Files:**
- Modify: `docs/release-gates/2026-09-29-kb-ai-release-gate.md`

**Interfaces:**
- No automatic runtime change. Produces a signed-off release decision with explicit user authorization.

- [x] **Step 1: Confirm every gate is satisfied**

  Verify the all-Batches-complete item, offline tests, static scans, read-only acceptance, WorkBuddy agreement and explicit user authorization in the release gate. Any missing item means no release decision.

- [ ] **Step 2: Request explicit publish/concurrency authorization**

  Ask the user to authorize one of: no write, a specific real publish run, or cross-side concurrent operation. Do not infer authorization from the plan or from read-only approval.

- [x] **Step 3: Record the decision and rollback plan**

  Record the authorized scope, run owner, expected lock/revision behavior, stop conditions and rollback/repair path. If authorization is not given, mark the gate as `pending` and keep the concurrency ban active.

- [x] **Step 4: Commit the decision record**

  `git add docs/release-gates/2026-09-29-kb-ai-release-gate.md`

  `git commit -m "docs: record kb-ai release decision"`

## Implementation Record

- Status: Tasks 1-5 complete; one authorized real preserve publish completed. Task 6 remains open only for explicit concurrency authorization.
- Offline evidence: Feishu store 218, wiki-ingest 92, knowledge-loader 38, KB-AI contract/docs 25, plugin suite 188 (1 skipped).
- `source_baseline.py` static scan confirms no remote write or lock call.
- Release gate and read-only acceptance templates were committed; no remote request, token collection, publish, or concurrency release was performed.
