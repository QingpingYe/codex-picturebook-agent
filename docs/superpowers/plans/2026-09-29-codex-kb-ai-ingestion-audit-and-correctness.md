# KB-AI 摄入审计与正确性实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 `NEW_SOURCES` 审计、容器计数、N2/N7/corrections 的明确适用性裁决，并让 CLI/文档只描述本仓真实运行路径。

**Architecture:** 在第二批的 `check_delta`/source-baseline 边界上增加稳定摘要字段和审计行；仓库中不存在的 WorkBuddy 步骤不新增伪实现，而由文档一致性测试固定为“不适用/未实现”。所有条件项先由代码搜索、测试和实际调用链裁决。

**Tech Stack:** Python 3、`unittest`、现有 wiki-ingest/store 脚本、Markdown 文档一致性测试。

**Spec:** `docs/superpowers/specs/2026-09-29-codex-kb-ai-ingestion-audit-and-correctness-design.md`

## Global Constraints

- `NEW_SOURCES` 只统计 `new` 内容节点，不计容器、`first_run` 或 excluded 节点。
- `NEW_SOURCES` 行即使发生解析降级也必须输出；零 `new` 固定为 `NEW_SOURCES: 0`。
- 容器没有真实正文下载路径时，不实现 N2 `char_count`/`pure_containers`，只记录不适用并加防回归测试。
- 不存在的“同步约束清单”和自动 `corrections-promote` 不得继续作为运行承诺。
- `--force-full` 只由调度方显式传入，不声称 Python 读取 `settings.json -> forceFullSync`。
- 本批不写远端、不修改第一批正文权威，也不重定义第二批准入语义。

## Review Focus

- `first_run`、容器或 excluded token 被误计入 `NEW_SOURCES`：由 Task 1 和 Task 2 的计数测试固定。
- 文档声称容器正文会被下载但代码不下载：由 Task 2 的调用链/文档测试固定。
- 把不存在的 N7 promotion 写成已实现：由 Task 3/4 的静态文档测试固定。
- `--force-full` 帮助继续承诺配置键：由 Task 5 的 parser/help 测试固定。
- 文档 CLI 清单与真实 parser 漂移：由 Task 6 的命令集合测试固定。

---

### Task 1: 新增 `NEW_SOURCES` 审计行

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/check_delta.py`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/test_check_delta.py`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/SKILL.md`

**Interfaces:**
- Produces: `new_source_tokens(result: Mapping[str, Any]) -> list[str]`.
- Produces: `build_new_sources_line(result: Mapping[str, Any]) -> str`.

- [ ] **Step 1: Write failing audit tests**

  Add tests:

  - `test_new_sources_zero_uses_fixed_line`
  - `test_new_sources_lists_content_tokens_sorted_and_unique`
  - `test_first_run_does_not_count_as_new`
  - `test_excluded_nodes_are_not_new_sources`
  - `test_new_sources_line_is_emitted_on_fallback`

  `build_new_sources_line()` must return exactly `NEW_SOURCES: 0` when no content token has verdict `new`, otherwise `NEW_SOURCES: N tokens=token1,token2`.

- [ ] **Step 2: Run the tests to verify failure**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest/scripts -p 'test_check_delta.py' -v`

  Expected: FAIL because the audit helpers and output do not exist.

- [ ] **Step 3: Implement the audit helpers and CLI output**

  Print the audit line immediately after the `DELTA` line in success and fallback paths. Do not let audit output alter verdicts or the process count.

- [ ] **Step 4: Document the audit line**

  `wiki-ingest/SKILL.md` must state that every plan prints `NEW_SOURCES`, including zero and degraded runs.

- [ ] **Step 5: Run the tests to verify pass**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest/scripts -p 'test_check_delta.py' -v`

  Expected: PASS.

- [ ] **Step 6: Commit**

  `git add plugins/picturebook-screenwriter/skills/wiki-ingest`

  `git commit -m "feat: audit new source tokens"`

### Task 2: 识别容器并裁决 N2 不适用

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/check_delta.py`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/test_check_delta.py`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/references/feishu-wiki-extraction.md`

**Interfaces:**
- Produces: `is_container(node: Mapping[str, Any]) -> bool`; true only for `has_child is True`.
- Modifies summary: `containers: int`.
- Does not add `pure_containers` or `char_count` unless a real container-body download path is found.

- [ ] **Step 1: Write failing container tests**

  Add tests:

  - `test_container_is_counted_not_processed`
  - `test_missing_has_child_is_not_container`
  - `test_containers_are_not_new_sources`
  - `test_container_never_receives_new_or_changed_verdict`
  - `test_delta_line_displays_containers`
  - `test_check_delta_has_no_container_body_download_path`

  The final test must inspect the parser/call sites and assert there is no `fetch_doc`/download command for containers.

- [ ] **Step 2: Run the tests to verify failure**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest/scripts -p 'test_check_delta.py' -v`

  Expected: FAIL because containers are currently indistinguishable from content nodes.

- [ ] **Step 3: Implement container partitioning**

  Partition `has_child is True` nodes out of the content verdict map, count them in `summary["containers"]`, and ensure they never enter `new_source_tokens`, `process`, `changed`, or `unknown`. No file body is fetched by `check_delta`.

- [ ] **Step 4: Record N2 as not applicable**

  Update docs: wiki-ingest only recurses containers, never downloads container body; N2 `char_count`/`pure_containers` is not applicable until a real body-download path is introduced.

- [ ] **Step 5: Run the tests to verify pass**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest/scripts -p 'test_check_delta.py' -v`

  Expected: PASS.

- [ ] **Step 6: Commit**

  `git add plugins/picturebook-screenwriter/skills/wiki-ingest`

  `git commit -m "refactor: separate source containers from content nodes"`

### Task 3: 清理 N7 约束词表的不存在步骤

**Files:**
- Create: `plugins/picturebook-screenwriter/tests/test_kb_ai_ingest_docs.py`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/references/structured-output-templates.md`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/SKILL.md`

**Interfaces:**
- No runtime API. Produces a docs consistency test that distinguishes a template concept from an executable step.

- [ ] **Step 1: Write failing documentation tests**

  Add:

  - `test_no_runtime_document_claims_sync_constraint_rebuild`
  - `test_redline_terms_section_is_marked_template_only`
  - `test_absent_sync_constraint_script_is_not_referenced`

  The tests read the two Markdown files and fail if they promise a “同步约束清单” or `sync_constraint_data.py` step that has no implementation.

- [ ] **Step 2: Run the tests to verify failure**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/tests -p 'test_kb_ai_ingest_docs.py' -v`

  Expected: FAIL on the existing “同步约束清单步骤” wording.

- [ ] **Step 3: Fix the documentation**

  Change the `redline_terms` block description to a template/machine-data convention only. State that automatic wordlist reconstruction is not implemented and, if added, must read the authoritative remote page before temporary-file cleanup.

- [ ] **Step 4: Run the tests to verify pass**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/tests -p 'test_kb_ai_ingest_docs.py' -v`

  Expected: PASS.

- [ ] **Step 5: Commit**

  `git add plugins/picturebook-screenwriter/tests/test_kb_ai_ingest_docs.py plugins/picturebook-screenwriter/skills/wiki-ingest`

  `git commit -m "docs: clarify unimplemented constraint rebuild"`

### Task 4: 固定 corrections 的“远端读取后追加”边界

**Files:**
- Modify: `plugins/picturebook-screenwriter/tests/test_kb_ai_ingest_docs.py`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/references/structured-output-templates.md`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/references/feishu-wiki-extraction.md`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/SKILL.md`

**Interfaces:**
- No automatic promotion API in this batch.
- Docs must state that any future promotion path must read current remote corrections content and append, never rebuild from an empty template.

- [ ] **Step 1: Add failing corrections contract tests**

  Add:

  - `test_corrections_promote_is_marked_unimplemented`
  - `test_corrections_candidate_does_not_claim_empty_template_replacement`
  - `test_future_correction_append_requires_remote_read`
  - `test_corrections_redline_terms_are_preserved`

  The tests assert the docs and candidate templates do not promise automatic promotion and explicitly preserve existing redline/wordlist content.

- [ ] **Step 2: Run the tests to verify failure**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/tests -p 'test_kb_ai_ingest_docs.py' -v`

  Expected: FAIL until the correction guidance is explicit.

- [ ] **Step 3: Update corrections guidance**

  Replace `corrections-promote` promises with a clear “not implemented” note. Add the future-path rule: read the current remote authority page, append the new card, preserve existing redlines and `redline_terms`, and only then publish/clean temporary files.

- [ ] **Step 4: Run the tests to verify pass**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/tests -p 'test_kb_ai_ingest_docs.py' -v`

  Expected: PASS.

- [ ] **Step 5: Commit**

  `git add plugins/picturebook-screenwriter/tests/test_kb_ai_ingest_docs.py plugins/picturebook-screenwriter/skills/wiki-ingest`

  `git commit -m "docs: constrain corrections promotion"`

### Task 5: 修正 A1 强制全量和 CLI 文档

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/check_delta.py`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/test_check_delta.py`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/references/feishu-wiki-extraction.md`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_store_cli.py`
- Modify: `plugins/picturebook-screenwriter/README.md`
- Modify: `README.md`

**Interfaces:**
- No new runtime behavior. Parser help and docs change only.
- Documented store commands must match the parser command set, including `source-baseline` from Batch 2.

- [ ] **Step 1: Write failing help/document tests**

  Add:

  - `test_force_full_help_requires_explicit_dispatch`
  - `test_force_full_help_does_not_claim_settings_key`
  - `test_store_cli_documented_commands_match_parser`
  - `test_no_fixed_workbuddy_command_count_is_stated`

- [ ] **Step 2: Run the tests to verify failure**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest/scripts -p 'test_check_delta.py' -v`

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p 'test_store_cli.py' -v`

  Expected: FAIL while the old `forceFullSync` help and command documentation remain.

- [ ] **Step 3: Update help and docs**

  Change `--force-full` help to: “调度方显式传 --force-full 时全部判 changed”. Update command lists by comparing them with `store_cli.build_parser()`; do not state a WorkBuddy-derived command count.

- [ ] **Step 4: Run the tests to verify pass**

  Run the commands from Step 2.

  Expected: PASS.

- [ ] **Step 5: Commit**

  `git add README.md plugins/picturebook-screenwriter/README.md plugins/picturebook-screenwriter/skills/wiki-ingest plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_store_cli.py`

  `git commit -m "docs: align force-full and cli behavior"`

### Task 6: 对齐 N4/C3/N6 和剩余摄入说明

**Files:**
- Modify: `plugins/picturebook-screenwriter/tests/test_kb_ai_ingest_docs.py`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/references/feishu-wiki-extraction.md`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/references/structured-output-templates.md`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/SKILL.md`

**Interfaces:**
- No runtime API. Docs must name the actual commands and authority paths.

- [ ] **Step 1: Add failing documentation tests**

  Add:

  - `test_download_rules_precede_staging_explanation`
  - `test_staging_versions_may_lag_without_drift`
  - `test_authority_paths_are_named_for_ingest_and_store`
  - `test_workbuddy_step_numbers_are_not_present`

- [ ] **Step 2: Run the tests to verify failure**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/tests -p 'test_kb_ai_ingest_docs.py' -v`

  Expected: FAIL until N4/C3/N6 wording reflects this repository.

- [ ] **Step 3: Rewrite the relevant sections**

  Explain download order before staging; state that staging vectors can lag without implying source drift; name the source snapshot, candidate, remote index, admission projection and page-footer paths; remove copied WorkBuddy step numbering.

- [ ] **Step 4: Run the tests to verify pass**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/tests -p 'test_kb_ai_ingest_docs.py' -v`

  Expected: PASS.

- [ ] **Step 5: Commit**

  `git add plugins/picturebook-screenwriter/tests/test_kb_ai_ingest_docs.py plugins/picturebook-screenwriter/skills/wiki-ingest plugins/picturebook-screenwriter/skills/feishu-knowledge-store/SKILL.md`

  `git commit -m "docs: align ingest audit terminology"`

### Task 7: 完整审计验收

**Files:**
- Review: all files changed by Tasks 1–6

**Interfaces:**
- No new runtime interface.

- [ ] **Step 1: Run affected suites**

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest/scripts -p 'test_*.py' -v`

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p 'test_*.py' -v`

  Run: `python -m unittest discover -s plugins/picturebook-screenwriter/tests -p 'test_kb_ai_ingest_docs.py' -v`

  Expected: PASS.

- [ ] **Step 2: Run static audit scans**

  Run: `rg -n "forceFullSync|同步约束清单|corrections-promote|pure_containers|char_count|NEW_SOURCES" plugins/picturebook-screenwriter docs/workbuddy-feishu-knowledge-base-compatibility.md`

  Expected: each occurrence is either implemented behavior, explicitly marked not applicable/unimplemented, or a test fixture.

- [ ] **Step 3: Record applicability decisions**

  Add an `Implementation Record` section to this plan with exact decisions for N2, N7, corrections, A1, N4/C3/N6 and CLI/stderr.

- [ ] **Step 4: Commit the verification record**

  `git add docs/superpowers/plans/2026-09-29-codex-kb-ai-ingestion-audit-and-correctness.md`

  `git commit -m "test: verify ingest audit batch"`