# KB-AI 内容权威与 D1 发布契约实施计划

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 将 Codex 侧 KB-AI 同步改为“候选正文是内容权威”：只按正文内容判断是否变化，纯排版标记差异保留，正文变化覆盖目标页，并以报告披露已覆盖的外部编辑。

**Architecture:** 保留现有远端租约、索引和页尾元数据作为控制面。新增一个只用于比较的语法感知 mark_insensitive(body)，由唯一的 SyncRunner 读取当前页后决定 publish、preserve 或 needs_review；Publisher 负责带当前 revision 的页面写入及写后回读，ControlPlane 负责索引和租约。冲突队列远端节点保持原位，但从初始化、解析、写入、CLI 和 lint 路径中摘除。

**Tech Stack:** Python 3、现有 unittest 测试套件、Feishu/Lark CLI 适配器、Markdown 与 JSON 页尾编解码。

**Spec:** docs/superpowers/specs/2026-09-29-codex-kb-ai-d1-content-authority-design.md

## Global Constraints

- 页面判定只比较候选正文与当前目标页正文的 content_signature；来源 revision、页尾字段和 Feishu revision 不能替代正文比较。
- mark_insensitive 只用于比较，不改写待发布正文；代码块、行内代码、普通链接目标、URL、字面标点和有意义的 *、_、# 必须保留。
- 页面写入使用当前 revision，并在写后回读正文、页尾和 revision；写请求的 revision 响应不能直接当作 last_seen_revision_id。
- 索引已有键但目标页不存在、索引无键但目标树已有同逻辑键页、页含资源/评论/未知块、索引损坏或锁未取得时，不得自动收编或覆盖。
- needs_review 表示本轮未能安全发布，下一轮可自然重试；它与冲突队列无关，archived 不由同步自动恢复。
- 报告删除 queued、conflicts、third_party_edits，新增 retried、overwritten_human_edits 和逐页 pages 结果；页面已写而索引未提交必须可见且不能报告为完全成功。
- source_edit_times 是兼容字段：索引/页尾可缺失，索引可为 null；同一来源 token 集合且候选时间不回退时，preserve 可只刷新索引时间，目标页零写入。
- 本批不改变源节点准入/排除规则，不退役本地 state/finalize，不实现跨侧并发放行；这些由后续批次处理。
- 现有远端 AI_KB_CONFLICT_QUEUE_V1 节点不删除、不改写，也不因缺失而创建；测试不得写入真实飞书。

## Review Focus

- 语法标记与正文字符混合：**词**、_词_、标题/引用前缀的变化应保留；代码、URL、字面 *、_、# 变化应发布。由 Task 1 的 comparator 测试和 Task 3 的页级决策测试固定。
- 来源向量未变但正文变化：必须发布，不能沿用旧 source_revisions 短路。由 Task 3 的回归测试固定。
- 页尾、索引和实际 revision 分离：last_seen_revision_id 取回读值，last_ai_revision_id 取已验证页尾值；响应 revision 与回读 revision 不同不能误报外部编辑。由 Task 2 的 publisher 测试固定。
- 页面写成、索引失败或回读不确定：报告必须落盘并区分 page_overwritten、index_committed 和未知值；下一轮只按远端证据受限恢复。由 Task 3 的故障恢复测试固定。
- 旧队列路径仍被隐式调用：不定位、不创建、不读取、不追加冲突页；CLI、fixture、lint 和说明文字均不能继续承诺入队。由 Task 4 的 CLI/lint/文档测试固定。

---

### Task 1: 正文比较器与可选来源时间 schema

**Files:**
- Modify: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/remote_markdown.py
- Modify: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/page_codec.py
- Modify: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/shared_schema.py
- Modify: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/models.py
- Test: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_remote_markdown.py
- Test: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_page_codec.py
- Test: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_shared_schema.py

**Interfaces:**
- Produces mark_insensitive(body: str) -> str for comparison only.
- Produces IndexEntry.source_edit_times: dict[str, int] | None.
- parse_candidate(markdown) maps optional frontmatter source_edit_time_parts to metadata["source_edit_times"]; absent input yields None.
- parse_remote_page() and render_remote_page() accept an optional source_edit_times object; page metadata without the field remains valid.
- Produces validate_edit_times(tokens: Iterable[str], edit_times: Iterable[int]) -> dict[str, int] for positive integer millisecond parts whose keys exactly match source_node_tokens.

- [x] Step 1: Write failing comparator tests. Add cases for paired emphasis markers, heading/quote prefixes, line-ending whitespace and equivalent [URL](URL); add negative cases for ordinary link target changes, code spans/fences, URL text, literal/unpaired * and _, and changed punctuation.
- [x] Step 2: Run comparator tests to verify the failure.

  Run: python -m unittest plugins.picturebook-screenwriter.skills.feishu-knowledge-store.scripts.test_remote_markdown -v

  Expected: FAIL because mark_insensitive is not defined and the new semantic cases have no implementation.
- [x] Step 3: Implement mark_insensitive(body: str) -> str. Protect fenced and inline code before normalization; only remove syntactically paired emphasis delimiters and valid line-level formatting prefixes; unescape Markdown punctuation outside protected regions; unwrap only [URL](URL); normalize trailing empty table cells and line-ending whitespace; preserve internal literal characters and URL targets. Trim only the formatting-only leading/trailing blank lines specified by the spec.
- [x] Step 4: Add failing optional-time schema tests. Cover candidate source_edit_time_parts list order, missing field, mismatched length, non-positive/bool values; cover remote page metadata missing/valid/invalid source_edit_times; cover IndexEntry default None.
- [x] Step 5: Run the schema tests to verify the failure.

  Run: python -m unittest plugins.picturebook-screenwriter.skills.feishu-knowledge-store.scripts.test_page_codec plugins.picturebook-screenwriter.skills.feishu-knowledge-store.scripts.test_shared_schema -v

  Expected: FAIL on the new optional field cases.
- [x] Step 6: Implement the schema changes. Extend metadata validation without weakening existing required-field checks; preserve canonical JSON ordering; reject invalid index/page maps and accept legacy missing/null index values as defined by the spec.
- [x] Step 7: Run Task 1 tests and the existing codec tests.

  Run: python -m unittest plugins.picturebook-screenwriter.skills.feishu-knowledge-store.scripts.test_remote_markdown plugins.picturebook-screenwriter.skills.feishu-knowledge-store.scripts.test_page_codec plugins.picturebook-screenwriter.skills.feishu-knowledge-store.scripts.test_shared_schema -v

  Expected: PASS.
- [x] Step 8: Commit the comparator and schema slice.

  git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/remote_markdown.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/page_codec.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/shared_schema.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/models.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_remote_markdown.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_page_codec.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_shared_schema.py
  git commit -m "feat: add content comparison and source time schema"

### Task 2: Control plane and publisher without conflict-page coupling

**Files:**
- Modify: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/control_plane.py
- Modify: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py
- Test: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_control_plane.py
- Test: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py

**Interfaces:**
- ControlPlane(cli, control_tokens, lock_ttl_minutes) continues to accept exactly {"index", "lock"}; _index_entry(), update_index() and rebuild_index() read/write optional source_edit_times.
- Publisher.initialize() and Publisher.resolve_control_plane() return content/index/lock tokens without requiring a conflict token. Existing conflict nodes are ignored and never created.
- Publisher.publish_new(entry, body, parent) -> IndexEntry carries optional source times into page metadata and records the verified post-create revision.
- Publisher.conditional_update(entry, current, candidate_markdown, source_revisions, source_edit_times=None) -> IndexEntry writes the candidate body, verifies metadata, and returns the post-write readback revision in last_seen_revision_id.
- Publisher raises NeedsReview for partial/warned/ambiguous writes; a possible write is re-read before any retry and is never blindly retried with the stale revision.

- [x] Step 1: Write failing control-plane tests. Add valid legacy/missing/null/valid source_edit_times index entries, reject wrong key sets, non-positive values and bools, and verify last_seen_revision_id may differ from last_ai_revision_id.
- [x] Step 2: Run the control-plane tests to verify the failure.

  Run: python -m unittest plugins.picturebook-screenwriter.skills.feishu-knowledge-store.scripts.test_control_plane -v

  Expected: FAIL on optional field parsing and the distinct revision semantics.
- [x] Step 3: Implement optional index parsing/rendering and preserve existing lease behavior. Keep corruption fail-closed and keep the remote lock as the only mutual exclusion mechanism.
- [x] Step 4: Write failing publisher tests. Cover initialization when the conflict node is absent, initialization when an old conflict node exists but is not fetched/created, optional page source times, response revision differing from readback revision, and a revision-conflict/uncertain-write path that fetches current content before retrying.
- [x] Step 5: Run publisher tests to verify the failure.

  Run: python -m unittest plugins.picturebook-screenwriter.skills.feishu-knowledge-store.scripts.test_publisher -v

  Expected: FAIL because initialization still requires the conflict page and conditional updates use the stale revision/readback assumptions.
- [x] Step 6: Refactor Publisher. Remove conflict-page creation/lookup/append helpers while leaving any existing remote node untouched; add source-time metadata handling; make conditional writes verify warnings, partial results, body signature, page metadata and readback revision; re-read before retrying after an ambiguous response.
- [x] Step 7: Run Task 2 tests and existing lease tests.

  Run: python -m unittest plugins.picturebook-screenwriter.skills.feishu-knowledge-store.scripts.test_control_plane plugins.picturebook-screenwriter.skills.feishu-knowledge-store.scripts.test_publisher -v

  Expected: PASS.
- [x] Step 8: Commit the control-plane/publisher slice.

  git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/control_plane.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_control_plane.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py
  git commit -m "refactor: publish AI-authoritative pages without conflict queue"

### Task 3: Content-authoritative SyncRunner, recovery and report contract

**Files:**
- Modify: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_runner.py
- Test: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_runner.py
- Test: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_end_to_end.py (rewrite the old SyncService scenario as a SyncRunner page/index handoff scenario)

**Interfaces:**
- The runner’s page decision is a pure _decide(candidate: Candidate, current: RemotePage) -> _Decision returning only publish, preserve, or needs_review.
- _Decision has action, reason and candidate_markdown fields; candidate_markdown is populated only for publish.
- _try_recover_index(indexed: IndexEntry, candidate: IndexEntry, current: RemotePage) -> IndexEntry | None is checked before ordinary metadata-drift rejection and returns an index entry only for the restricted remote recovery case.
- Existing source-vector short-circuit and historical fetch_revision()/three-way merge calls are removed from the executable path.
- SyncReport.as_dict() keeps the existing top-level Codex fields status, candidates, published, preserved, failed, run_id, source, errors, and adds retried, overwritten_human_edits, and pages; it emits no queued, conflicts, or third_party_edits.
- Each pages[] item contains key, action, reason, before_revision, after_revision, page_overwritten, and index_committed; the last two and unknown revisions may be null.
- A successful page write followed by index failure is failed, not published; a subsequent run may repair only an existing indexed key when page key, page metadata, candidate signature, source revisions and newer AI revision all match.
- needs_review entries are retryable on later runs; archived entries are not auto-restored.

- [x] Step 1: Replace queue/source-vector tests with failing content-authority tests in test_sync_runner.py. Add tests for same source vector + changed body -> publish; changed source vector + equal body after Feishu formatting -> preserve; marker-only differences -> preserve; literal marker/code/URL/punctuation changes -> publish; no-index/no-page -> first publish; no-index/existing-page -> review failure; archived -> no write.
- [x] Step 2: Add failing report and overwrite-disclosure tests. Assert no legacy queue fields, exact pages[] fields, overwritten_human_edits only after a confirmed overwrite when the pre-write revision had advanced, no disclosure for preserve/review, and published counts only page+index success.
- [x] Step 3: Add failing revision/recovery tests. Simulate revision conflict and assert the runner re-fetches and re-decides; simulate page success/index failure and assert a failed report is written with page/index flags; on the next run, assert a qualifying remote page repairs the index without another page write, while a non-qualifying page remains needs_review.
- [x] Step 4: Run the new runner tests to verify the failure.

  Run: python -m unittest plugins.picturebook-screenwriter.skills.feishu-knowledge-store.scripts.test_sync_runner -v

  Expected: FAIL because the current runner short-circuits on source revisions, queues failures, uses three-way merge, and raises before persisting a complete report.
- [x] Step 5: Implement the new runner flow. Read and validate the remote index under lease; attempt the §4 restricted recovery before normal metadata/index mismatch rejection; compare candidate/current bodies with mark_insensitive; call Publisher only for actual content changes; refresh same-source-set index times on preserve without writing the page; record per-page outcomes and continue reporting failures without falsely claiming success.
- [x] Step 6: Implement index/status handling. Mark safely reportable failed entries needs_review, restore published after a successful later preserve/publish, leave archived unchanged, and use only remote index/page/candidate data for recovery.
- [x] Step 7: Run runner, end-to-end and all feishu-store tests.

  Run: python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p 'test_*.py' -v

  Expected: PASS, with old queue/merge tests removed or replaced by the new contract tests.
- [x] Step 8: Commit the content-authority runner slice.

  git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_runner.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_runner.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_end_to_end.py
  git commit -m "feat: make KB-AI content authoritative"

### Task 4: Remove obsolete queue/merge entry points and align CLI, lint and skills

**Files:**
- Modify: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/store_cli.py
- Modify: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/contract_lint.py
- Modify: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_store_cli.py
- Modify: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_contract_lint.py
- Modify: plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/wiki_lint.py
- Modify: plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/test_wiki_lint.py
- Modify: plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/load_knowledge.py
- Modify: plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/test_load_knowledge.py
- Modify: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/SKILL.md
- Modify: plugins/picturebook-screenwriter/skills/knowledge-loader/SKILL.md
- Delete: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/merge_protocol.py
- Delete: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_knowledge.py
- Delete: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/target_state.py
- Delete: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_merge_protocol.py
- Delete: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_knowledge.py
- Delete: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_target_state.py

**Interfaces:**
- store_cli.build_parser() exposes no conflict-list or conflict-append; lint-fixture exports tree/index/pages only.
- contract_lint.run_lint(fixture) performs the remaining index/tree/page checks and never requires conflict.md.
- wiki_lint.lint_index() and KnowledgeLoader warnings use neutral “待复核/同步未完成” language, not “待处理冲突”.
- The Feishu store and knowledge-loader skill text describes AI content authority, retryable needs_review, no queue and no human-priority merge. Other domain-level “conflict” concepts in relevance/screening remain unchanged.

- [x] Step 1: Write failing CLI/lint/documentation tests. Assert old CLI commands are rejected, fixtures do not fetch/export the conflict page, valid fixtures without conflict.md pass, malformed remaining index/page/tree data still fails, and needs_review warning text is neutral.
- [x] Step 2: Run the CLI/lint tests to verify the failure.

  Run: python -m unittest plugins.picturebook-screenwriter.skills.feishu-knowledge-store.scripts.test_store_cli plugins.picturebook-screenwriter.skills.feishu-knowledge-store.scripts.test_contract_lint plugins.picturebook-screenwriter.skills.wiki-ingest.scripts.test_wiki_lint -v

  Expected: FAIL because the current CLI and fixture lint still require and expose the queue.
- [x] Step 3: Remove queue commands and fixture dependency. Keep the remote queue node untouched by simply never resolving it; remove parser branches, append/list behavior, conflict fixture export and conflict-record validation.
- [x] Step 4: Delete the old merge/service modules and their tests after all imports are gone. Verify no executable module under the Feishu store imports merge_protocol, sync_knowledge, target_state, append_conflict, or report field queued.
- [x] Step 5: Update load_knowledge.py, its tests, the skill text and wiki lint wording. State that AI candidate content is authoritative, needs_review is a retryable safety state, and no conflict queue/manual merge is consulted. Do not alter unrelated relevance/screening conflict terminology.
- [x] Step 6: Run Task 4 tests and repository reference scans.

  Run: python -m unittest plugins.picturebook-screenwriter.skills.feishu-knowledge-store.scripts.test_store_cli plugins.picturebook-screenwriter.skills.feishu-knowledge-store.scripts.test_contract_lint plugins.picturebook-screenwriter.skills.wiki-ingest.scripts.test_wiki_lint -v

  Then: rg -n "merge_protocol|sync_knowledge|target_state|append_conflict|\bqueued\b|third_party_edits|conflict-list|conflict-append" plugins/picturebook-screenwriter/skills/feishu-knowledge-store plugins/picturebook-screenwriter/skills/wiki-ingest

  Expected: tests PASS; the scan has no executable/contract references (historical test fixtures or unrelated domain conflict terms must be removed or explicitly excluded).
- [x] Step 7: Run the complete plugin test suite.

  Run: python -m unittest discover -s plugins/picturebook-screenwriter -p 'test_*.py' -v

  Expected: PASS with no old queue/merge contract tests remaining.
- [x] Step 8: Commit the CLI/lint/documentation slice.

  git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store plugins/picturebook-screenwriter/skills/knowledge-loader plugins/picturebook-screenwriter/skills/wiki-ingest
  git commit -m "chore: retire conflict queue and merge entry points"

### Task 5: Final contract verification and release gate

**Files:**
- Review: all files touched by Tasks 1–4
- Test: existing Feishu store, wiki-ingest and knowledge-loader test suites

**Interfaces:**
- No new runtime interface; this task verifies the cross-module contract before any authorized remote run.

- [x] Step 1: Run targeted contract tests again from a clean workspace.

  Run: python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p 'test_*.py' -v

  Expected: PASS.
- [x] Step 2: Run cross-skill tests that consume index status and page metadata.

  Run: python -m unittest discover -s plugins/picturebook-screenwriter/skills/knowledge-loader -p 'test_*.py' -v
  python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest -p 'test_*.py' -v

  Expected: PASS, with neutral needs_review messaging and optional source-time fields accepted.
- [x] Step 3: Perform a static contract scan.

  Run: rg -n "queued|third_party_edits|conflict-list|conflict-append|append_conflict|merge_protocol|sync_knowledge|target_state" plugins/picturebook-screenwriter/skills/feishu-knowledge-store plugins/picturebook-screenwriter/skills/wiki-ingest plugins/picturebook-screenwriter/skills/knowledge-loader

  Expected: no active D1 queue/merge references; unrelated content-quality/relevance conflict fields remain outside this contract.
- [x] Step 4: Review the generated report fixtures manually. Confirm that a failed page/index handoff is represented with status=failed, page_overwritten, index_committed, overwritten_human_edits, and nulls for unknown observations.
- [x] Step 5: Stop before any real Feishu write. A remote read-only comparison may be reviewed separately; real synchronization remains blocked until the later source-state and admission batches are complete and the user explicitly authorizes the run.
## Implementation Record

- Commit: `91e5937 feat: make KB-AI content authoritative`.
- Status: offline implementation complete; remote write acceptance intentionally not run.
- Verification: 199 Feishu store tests, 76 wiki-ingest tests and 38 knowledge-loader tests passed.
- Retirement scan: old queue/merge identifiers remain only in tests that assert their absence or rejection.
