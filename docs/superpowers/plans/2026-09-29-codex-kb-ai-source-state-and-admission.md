# KB-AI 来源状态、候选时效与源准入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将来源增量判据迁移到远端索引时间/revision 证据，补齐候选时效校验，并实现 `AI_KB_SOURCE_ADMISSION_V1` 的默认纳入与子树排除。

**Architecture:** store 侧新增只读 `source-baseline` 投影，把已验证索引和准入页写入本轮 run 目录；wiki-ingest 侧消费该投影，纯函数完成准入展开、token 级远端基线合并和 fail-safe 分类。远端索引继续是同步状态权威，准入页只读，本地 `delta_state.json` 在功能替代后退役。

**Tech Stack:** Python 3、`unittest`、现有 `LarkCli`/`ControlPlane`/`Publisher` 边界、Markdown + JSON 控制页、飞书 Wiki 节点快照。

**Spec:** `docs/superpowers/specs/2026-09-29-codex-kb-ai-source-state-and-admission-design.md`

## Global Constraints

- 未裁定源节点默认纳入；只有最新有效 `exclude` 生效，并作用于节点自身、全部后代和后代容器。
- 准入页缺失是空策略；准入页存在但 schema 无效、字段未知或决定顺序不明确时硬失败。
- `source-baseline` 只读远端索引和准入页，不获取或修改锁，不写任何远端文档。
- 本地 `delta_state.json` 最终必须退役；切换完成前不得靠本地 state 决定 skip。
- `unchanged` 只有在 edit time、适用 revision 和缓存哈希都提供正向证据时成立。
- 没有足够证据时判 `unknown` 或 `changed`，按 fail-safe 重新读取，不得漏掉变化源。
- `--only` 不推断 deleted；命中 excluded token 时拒绝执行。
- 本批不执行真实飞书写入，不解除 Codex/WorkBuddy 并发禁令。

## Review Focus

- 准入字段或决定顺序损坏时被误当空策略：由 Task 1 的 schema/顺序测试固定。
- 排除容器后仍递归或下载后代：由 Task 1 和 Task 5 的子树测试固定。
- token 时间缺失、索引证据冲突或 revision 不可确认时仍判 `unchanged`：由 Task 4 的 fail-safe 测试固定。
- 本地 state 删除一半，仍有调用方读取或 finalize：由 Task 6 的 CLI/引用扫描固定。
- 候选时间与快照不一致仍生成 manifest：由 Task 3 的 B1 校验固定。

---

### Task 1: 固化准入契约并实现纯策略解析

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/source_admission.py`
- Create: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_source_admission.py`
- Create: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/fixtures/source_admission_v1.json`

**Interfaces:**
- Produces: `AdmissionEntry`, `AdmissionPolicy`, `AdmissionResult` immutable dataclasses.
- Produces: `parse_source_admission(content: str) -> AdmissionPolicy`.
- Produces: `resolve_source_admission(policy: AdmissionPolicy, snapshot: Mapping[str, Any]) -> AdmissionResult`.
- Fixture fields must match WorkBuddy's six-field contract exactly. This plan uses `node_token`, `title`, `decision`, `reason`, `decided_at`, `decided_by`; if the captured contract differs, update the fixture and constants in the same task before implementation continues.

- [ ] **Step 1: Capture the read-only admission fixture**

  Run an authorized read-only fetch of `AI_KB_SOURCE_ADMISSION_V1`. Save a minimized fixture containing one `admit` and one `exclude`, preserving exact field names and decision timestamps. If no real sample is available, stop Task 1 and request the WorkBuddy fixture; do not guess.

- [ ] **Step 2: Write failing parser and tree tests**

  Add tests:

  - `test_missing_page_is_empty_policy`
  - `test_fixture_has_exact_six_fields`
  - `test_unknown_field_and_wrong_schema_version_fail`
  - `test_latest_admit_cancels_previous_exclude`
  - `test_equal_decided_at_conflict_fails`
  - `test_exclude_marks_node_container_and_all_descendants`
  - `test_empty_policy_does_not_require_complete_ancestry`
  - `test_noncritical_missing_parent_or_cycle_stops`

  Assert `resolve_source_admission(...).excluded_tokens` contains the excluded node and every descendant, and `excluded_containers` contains every excluded container.

- [ ] **Step 3: Run the tests to verify failure**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p 'test_source_admission.py' -v`

  Expected: FAIL because `source_admission.py` and the fixture do not exist.

- [ ] **Step 4: Implement strict parsing and tree resolution**

  `parse_source_admission()` must accept the exact `# AI_KB_SOURCE_ADMISSION_V1` JSON block, reject unknown/missing fields, and reject invalid `decision` values. Resolve decisions by `decided_at`; equal timestamps for competing decisions are a conflict. Build descendants only from `parent_node_token`; missing parents/cycles stop the run only when the policy contains at least one effective exclusion.

- [ ] **Step 5: Run the tests to verify pass**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p 'test_source_admission.py' -v`

  Expected: PASS.

- [ ] **Step 6: Commit**

  `git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/source_admission.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_source_admission.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/fixtures/source_admission_v1.json`

  `git commit -m "feat: add source admission policy resolver"`

### Task 2: 新增只读 source-baseline 投影

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/source_baseline.py`
- Create: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_source_baseline.py`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/control_plane.py`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/store_cli.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_control_plane.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_store_cli.py`

**Interfaces:**
- Produces: `build_source_baseline(index_revision_id: int, entries: Mapping[str, IndexEntry], admission: AdmissionPolicy | None) -> dict[str, Any]`.
- Produces: `write_source_baseline(path: str | Path, payload: Mapping[str, Any]) -> None`.
- Modifies: `ControlPlane.read_index_with_revision() -> tuple[int, dict[str, IndexEntry]]`; `read_index()` delegates to it.
- Modifies: `Publisher.resolve_control_plane(require_admission: bool = False) -> dict[str, str]`; `admission` is present only when the page exists or `require_admission=True`.
- Adds command: `store_cli.py source-baseline --config ... --workspace ... --out <path>`.

- [ ] **Step 1: Write failing projection and CLI tests**

  Add tests:

  - `test_source_baseline_projection_preserves_raw_revisions_and_times`
  - `test_missing_admission_projects_null`
  - `test_malformed_admission_fails_command`
  - `test_source_baseline_never_calls_update_or_create`
  - `test_read_index_with_revision_returns_document_revision`
  - `test_resolve_control_plane_keeps_admission_optional`

  Assert the JSON includes `schema_version`, `index_revision_id`, `entries`, and `admission`; entries contain only `key`, `source_revisions`, and `source_edit_times`.

- [ ] **Step 2: Run the tests to verify failure**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p 'test_source_baseline.py' -v`

  Expected: FAIL because the module and command do not exist.

- [ ] **Step 3: Implement the read-only projection**

  Add `source-baseline` to the parser and dispatch. It resolves optional admission, reads index with revision, validates both payloads, writes atomically to `--out`, and prints `{"out": "...", "index_revision_id": N}`. It must not call `update_doc`, `create_doc`, `create_space_doc`, acquire or release a lock.

- [ ] **Step 4: Run projection, control-plane, publisher and CLI tests**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p 'test_source_baseline.py' -v`

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p 'test_control_plane.py' -v`

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p 'test_publisher.py' -v`

  Expected: PASS.

- [ ] **Step 5: Commit**

  `git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts`

  `git commit -m "feat: export read-only source baseline"`

### Task 3: 补齐候选编辑时间与 B1 校验

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/generate_entries.py`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/test_generate_entries.py`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/references/structured-output-templates.md`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/references/feishu-wiki-extraction.md`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/SKILL.md`

**Interfaces:**
- Produces: `source_edit_time_vector(frontmatter: dict[str, object]) -> dict[str, int]`.
- Produces: `validate_candidate_times(frontmatter: Mapping[str, Any], snapshot: Mapping[str, Any]) -> list[tuple[str, str]]`.
- Modifies: `validate_file(filepath: str, snapshot: Mapping[str, Any] | None = None)`.
- Modifies CLI: `--nodes` is required for `--validate-only`; valid when supplied with manifest generation.

- [ ] **Step 1: Extend the fixture helper and write failing tests**

  Add `source_edit_time_parts` to `_file()`. Add tests:

  - `test_valid_edit_time_vector_pairs_tokens`
  - `test_edit_time_length_mismatch_fails`
  - `test_edit_time_must_be_positive_integer`
  - `test_validate_only_requires_nodes_argument`
  - `test_validate_only_exit_2_when_nodes_unreadable`
  - `test_candidate_time_mismatch_fails`
  - `test_missing_snapshot_time_warns`
  - `test_manifest_entry_contains_source_edit_times`

- [ ] **Step 2: Run the tests to verify failure**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest/scripts -p 'test_generate_entries.py' -v`

  Expected: FAIL because time parsing and `--nodes` do not exist.

- [ ] **Step 3: Implement the time vector and freshness validator**

  Use `shared_schema.validate_edit_times`. Make `source_edit_time_parts` a required candidate field. `--validate-only` returns `2` for missing/unreadable `--nodes`; per-candidate mismatch is `FAIL`; missing snapshot time is `WARN`. Add `source_edit_times` to manifest entries.

- [ ] **Step 4: Update templates and ingest instructions**

  All templates must show the ordered time list. Step 5/6 of `wiki-ingest/SKILL.md` must require the list and call `generate_entries.py --validate-only --nodes <snapshot>` before handing off.

- [ ] **Step 5: Run the tests to verify pass**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest/scripts -p 'test_generate_entries.py' -v`

  Expected: PASS.

- [ ] **Step 6: Commit**

  `git add plugins/picturebook-screenwriter/skills/wiki-ingest`

  `git commit -m "feat: validate candidate source edit times"`

### Task 4: 用远端 source baseline 重写 check_delta

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/check_delta.py`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/test_check_delta.py`

**Interfaces:**
- Consumes: source-baseline JSON from Task 2.
- Produces: `load_source_baseline(path) -> Mapping[str, Any] | None`.
- Produces: `build_token_baseline(payload) -> dict[str, dict[str, Any]]`.
- Modifies: `classify(snapshot, source_baseline, cache_dir=None, force_full=False, only=None)`.

- [ ] **Step 1: Replace state-based tests with failing remote-baseline tests**

  Add tests:

  - `test_missing_baseline_is_first_run`
  - `test_token_absent_from_index_is_new`
  - `test_matching_time_revision_and_cache_is_unchanged`
  - `test_missing_index_time_forces_changed`
  - `test_conflicting_index_times_force_changed`
  - `test_doc_revision_mismatch_forces_changed`
  - `test_file_without_revision_can_skip_with_time_and_cache`
  - `test_index_token_missing_from_snapshot_is_deleted`
  - `test_only_never_infers_deleted`

  Assert no skip can result from `edit_time_ms` equality alone.

- [ ] **Step 2: Run the tests to verify failure**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest/scripts -p 'test_check_delta.py' -v`

  Expected: FAIL because `classify()` still accepts local state.

- [ ] **Step 3: Implement token baseline and fail-safe classification**

  Build token evidence by scanning raw index entries. Mark token evidence inconsistent when times or revisions disagree, or when an entry has `source_edit_times=null`. Use `source_revisions` as the applicable revision baseline for `docx/wiki`; files may skip on time + cache hash. Detect `deleted` only when a token appears in the remote index and is absent from the current snapshot.

- [ ] **Step 4: Run the tests to verify pass**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest/scripts -p 'test_check_delta.py' -v`

  Expected: PASS.

- [ ] **Step 5: Commit**

  `git add plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/check_delta.py plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/test_check_delta.py`

  `git commit -m "feat: classify delta from remote source baseline"`

### Task 5: 将准入子树排除接入增量分类

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/check_delta.py`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/test_check_delta.py`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/SKILL.md`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_source_admission.py`

**Interfaces:**
- Consumes: `parse_source_admission()` and `resolve_source_admission()` from Task 1.
- Produces: result keys `excluded`, `excluded_containers`, and per-token verdict `excluded`.

- [ ] **Step 1: Write failing integration tests**

  Add tests:

  - `test_unadmitted_node_is_processed`
  - `test_excluded_leaf_is_not_downloaded`
  - `test_excluded_container_and_descendants_are_not_processed`
  - `test_future_child_inherits_excluded_ancestor`
  - `test_only_on_excluded_token_exits_2`
  - `test_corrupt_admission_baseline_stops_ingest`

- [ ] **Step 2: Run the tests to verify failure**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest/scripts -p 'test_check_delta.py' -v`

  Expected: FAIL because `check_delta.py` does not consume admission.

- [ ] **Step 3: Integrate admission resolution before classification**

  Parse admission from the projection. Resolve the excluded set before classifying tokens. Excluded tokens receive `{"verdict": "excluded", "reason": ...}` when reported, but are never counted as `process`. Excluded containers are included in `excluded_containers`. `--only` checks exclusions before normal processing and returns exit code `2` through a typed error or parser-level failure.

- [ ] **Step 4: Update ingest instructions**

  Step 2/3 of `wiki-ingest/SKILL.md` must state that containers and excluded nodes do not enter download, external-link scanning or candidate generation.

- [ ] **Step 5: Run the tests to verify pass**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest/scripts -p 'test_check_delta.py' -v`

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p 'test_source_admission.py' -v`

  Expected: PASS.

- [ ] **Step 6: Commit**

  `git add plugins/picturebook-screenwriter/skills/wiki-ingest plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/source_admission.py`

  `git commit -m "feat: apply source admission during ingest"`

### Task 6: 退役本地持久 state 与 finalize

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/check_delta.py`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/test_check_delta.py`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/references/feishu-wiki-extraction.md`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/references/structured-output-templates.md`

**Interfaces:**
- Removes: `merge_state`, `--state`, `--write-state`, `--hash-map`, `--revision-map`, and finalize mode.
- Keeps: `--nodes`, `--source-baseline`, `--cache-dir`, `--force-full`, `--only`, `--out`.

- [ ] **Step 1: Write failing retirement tests**

  Add parser tests that each retired option exits with code `2`. Add a static test in `test_check_delta.py` that read `check_delta.py` and asserts it does not contain `merge_state`, `delta_state.json`, `--write-state`, `--hash-map`, `--revision-map`, or `finalize 阶段`.

- [ ] **Step 2: Run the tests to verify failure**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest/scripts -p 'test_check_delta.py' -v`

  Expected: FAIL while legacy symbols remain.

- [ ] **Step 3: Remove the legacy code paths**

  Delete `merge_state` and all writeback branches. Remove stale imports/constants. Update the module docstring to describe only `--source-baseline` plan mode. Update run-directory layout and delete instructions so `delta_state.json` is no longer created or mentioned.

- [ ] **Step 4: Scan for consumers**

  Run: `rg -n "merge_state|delta_state|--state|--write-state|--hash-map|--revision-map|finalize" plugins/picturebook-screenwriter/skills/wiki-ingest`

  Expected: no runtime references; historical notes are removed or explicitly marked not applicable.

- [ ] **Step 5: Run the full wiki-ingest suite**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest/scripts -p 'test_*.py' -v`

  Expected: PASS.

- [ ] **Step 6: Commit**

  `git add plugins/picturebook-screenwriter/skills/wiki-ingest`

  `git commit -m "refactor: retire local delta state"`

### Task 7: 端到端接口与兼容说明

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/README.md`
- Modify: `README.md`
- Modify: `docs/workbuddy-feishu-knowledge-base-compatibility.md`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_end_to_end.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_store_cli.py`

**Interfaces:**
- Consumes all interfaces from Tasks 1–6.
- Produces a documented operator sequence: `source-baseline` → `check_delta` → candidate generation → `prepare`/`publish`.

- [ ] **Step 1: Add failing end-to-end tests**

  Add `test_source_baseline_to_delta_to_publish_contract` in `test_end_to_end.py`: build a fake index/admission, invoke the projection, classify a matching token as unchanged, and classify a time-mismatched token as changed. Include one excluded token and assert no candidate is produced for it.

- [ ] **Step 2: Run the test to verify failure**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p 'test_end_to_end.py' -v`

  Expected: FAIL until the modules and documented interfaces are connected.

- [ ] **Step 3: Wire the documented operator sequence**

  Update store and ingest skill steps to call `store_cli.py source-baseline` before `check_delta.py`, pass `--source-baseline`, and run `generate_entries.py --validate-only --nodes`. Document missing admission as empty policy and corrupt admission as hard failure.

- [ ] **Step 4: Run targeted end-to-end and CLI tests**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p 'test_end_to_end.py' -v`

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p 'test_store_cli.py' -v`

  Expected: PASS.

- [ ] **Step 5: Commit**

  `git add README.md plugins/picturebook-screenwriter/README.md plugins/picturebook-screenwriter/skills/feishu-knowledge-store docs/workbuddy-feishu-knowledge-base-compatibility.md`

  `git commit -m "docs: document remote source baseline workflow"`

### Task 8: 完整验收与旧边界扫描

**Files:**
- Review: all files changed by Tasks 1–7

**Interfaces:**
- No new runtime interface.

- [ ] **Step 1: Run all affected suites**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p 'test_*.py' -v`

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest/scripts -p 'test_*.py' -v`

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/knowledge-loader/scripts -p 'test_*.py' -v`

  Expected: PASS.

- [ ] **Step 2: Run the retirement and contract scans**

  Run: `rg -n "delta_state|merge_state|--write-state|--hash-map|--revision-map|forceFullSync" plugins/picturebook-screenwriter docs/workbuddy-feishu-knowledge-base-compatibility.md`

  Expected: no active runtime or current-contract references.

- [ ] **Step 3: Verify no remote-write call was added to source projection**

  Run: `rg -n "update_doc|create_doc|create_space_doc|acquire_lock|release_lock" plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/source_baseline.py`

  Expected: no matches.

- [ ] **Step 4: Record the verification result**

  Add a short `Implementation Record` section to this plan with commit range, suite counts, and unresolved remote-readiness items. Do not claim remote acceptance.

- [ ] **Step 5: Commit the verification record**

  `git add docs/superpowers/plans/2026-09-29-codex-kb-ai-source-state-and-admission.md`

  `git commit -m "test: verify source state and admission batch"`