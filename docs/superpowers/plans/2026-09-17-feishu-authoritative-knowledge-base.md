# Feishu Authoritative Knowledge Base Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Replace the WorkBuddy library dependency with a shared Feishu Wiki knowledge store that safely publishes wiki-ingest output and supplies authoritative knowledge to the picture-book writing plugin.

**Architecture:** wiki-ingest turns the source Wiki into validated Markdown candidates. A standard-library-only Python feishu-knowledge-store adapter uses lark-cli to manage target Wiki docx pages, remote control documents, a revision-protected lease, and document-level conditional writes. A read-only knowledge-loader provides current Feishu evidence to the writing workflow.

**Tech Stack:** Codex plugin skills, Python 3.10+ standard library and unittest, Node.js for the existing staging planner, Feishu/Lark CLI 1.0.95+, Feishu docx pages.

**Spec:** docs/superpowers/specs/2026-09-17-feishu-authoritative-knowledge-base-design.md

## Global Constraints

- The target Wiki rooted at T08vwqXroiuJEfkoVzFcRaFXnMf is the only shared authority; local state is disposable cache only.
- The original Feishu Wiki is read-only wiki-ingest input. Never write source material back to it.
- Store shared knowledge as docx pages and update only through docs +update --revision-id <current revision>. Never use unconditional markdown +overwrite for these pages.
- Human edits are authoritative. A source conflict retains the current page and creates a conflict record.
- Index, lock, and conflict queue live remotely below 99_系统控制台. Validate their JSON before every write and fail closed if malformed.
- Default to lark-cli --as user. Resolve the binary in order: LARK_CLI_PATH, lark-cli on PATH, then D:\lark-cli\lark-cli.exe.
- Do not require WorkBuddy, shared local storage, a database, third-party Python packages, or a bundled CLI.
- A page with comments, resources, unknown blocks, or malformed metadata is needs_review and never automatically overwritten.
- All user-facing skill messages and generated documentation are Chinese.

---

## File structure

| Path | Responsibility |
| --- | --- |
| plugins/picturebook-screenwriter/config/feishu-knowledge-base.example.json | Non-secret source/target/identity/lease template. |
| plugins/picturebook-screenwriter/skills/feishu-knowledge-store/SKILL.md | Agent protocol for preflight, initialize, prepare, decision, apply, recovery. |
| plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/models.py | Config, snapshot, index, lease, work-item, and decision dataclasses. |
| plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/config.py | Config resolution and validation. |
| plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/lark_cli.py | Testable typed lark-cli subprocess boundary. |
| plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/page_codec.py | Candidate parsing, logical keys, reader/system metadata envelopes. |
| plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/control_plane.py | Remote index, revision-based lease, recovery scan. |
| plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py | Wiki initialization, docx publishing, navigation/log/queue writes. |
| plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/merge_protocol.py | Deterministic merge classification and decision validation. |
| plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_knowledge.py | preflight, initialize, prepare, validate-decisions, apply commands. |
| plugins/picturebook-screenwriter/skills/wiki-ingest/ | Portable candidate-only source-ingestion skill, references, generic scripts, tests. |
| plugins/picturebook-screenwriter/skills/knowledge-loader/ | Read-only target-Wiki retrieval skill, script, tests. |
| plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/cache.py | Per-user, timestamped offline knowledge cache. |
| plugins/picturebook-screenwriter/skills/picturebook-screenwriter/SKILL.md | Updated sync routing and creation-time knowledge loading. |
| plugins/picturebook-screenwriter/.codex-plugin/plugin.json and both READMEs | Marketplace metadata and multi-user operator docs. |

Task-local work bundles only may be created below <workspace>/.picturebook-screenwriter/tmp/<run_id>/. Delete them after reporting; never use them as shared state.

### Task 1: Create portable config, shared models, and Lark CLI boundary

**Files:**
- Create: plugins/picturebook-screenwriter/config/feishu-knowledge-base.example.json
- Create: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/models.py
- Create: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/config.py
- Create: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/lark_cli.py
- Test: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_config.py
- Test: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_lark_cli.py

**Interfaces:**
- Produces load_config(config_path, workspace, environ) -> KnowledgeConfig.
- Produces LarkCli(preferred_binary, identity, runner). Its public methods are preflight, get_node, list_nodes, create_doc, fetch_doc, fetch_doc_revision, and update_doc.
- `preflight()` verifies the configured target root and the configured source root, so every run can prove both the write target and read-only source are reachable.
- No later script may call subprocess.run directly.

- [ ] **Step 1: Write the failing config tests.**

~~~python
class ConfigTests(unittest.TestCase):
    def test_environment_path_wins(self):
        path = write_config(self.tmp, {
            "schema_version": 1,
            "source_wiki_url": "https://wcno1rbz0o8i.feishu.cn/wiki/OI9gwaRv8i3RwOkGBNnc7VHinDd",
            "target_root_token": "T08vwqXroiuJEfkoVzFcRaFXnMf",
            "identity": "user",
            "lock_ttl_minutes": 45,
        })
        config = load_config(None, self.tmp, {"PICTUREBOOK_KB_CONFIG": str(path)})
        self.assertEqual(config.target_root_token, "T08vwqXroiuJEfkoVzFcRaFXnMf")

    def test_bot_identity_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "identity must be 'user'"):
            load_config(str(write_config(self.tmp, {"identity": "bot"})), self.tmp, {})
~~~

- [ ] **Step 2: Run the test to verify failure.**

Run: python -m unittest plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_config.py -v  
Expected: FAIL because config.py does not exist.

- [ ] **Step 3: Implement strict models and config parser.**

~~~python
@dataclass(frozen=True)
class KnowledgeConfig:
    source_wiki_url: str
    target_root_token: str
    identity: Literal["user"]
    lock_ttl_minutes: int
    cli_candidates: tuple[Path, ...]

@dataclass(frozen=True)
class IndexEntry:
    key: str
    doc_token: str
    wiki_node_token: str
    source_revisions: dict[str, str]
    last_ai_revision_id: int
    last_seen_revision_id: int
    status: Literal["published", "needs_review", "archived"]
~~~

Accept only schema_version 1 and the five config fields shown in the test. Reject empty source URL/root token and lease values outside 15–120. Build CLI candidates from LARK_CLI_PATH, shutil.which("lark-cli"), then D:\lark-cli\lark-cli.exe, omitting duplicates.

- [ ] **Step 4: Add the distributable config template.**

~~~json
{
  "schema_version": 1,
  "source_wiki_url": "https://wcno1rbz0o8i.feishu.cn/wiki/OI9gwaRv8i3RwOkGBNnc7VHinDd",
  "target_root_token": "T08vwqXroiuJEfkoVzFcRaFXnMf",
  "identity": "user",
  "lock_ttl_minutes": 45
}
~~~

- [ ] **Step 5: Write failing CLI contract tests.**

~~~python
class LarkCliTests(unittest.TestCase):
    def test_update_uses_current_revision_as_precondition(self):
        runner = FakeRunner(ok({"data": {"document": {"revision_id": 13}}}))
        client = LarkCli(Path("lark-cli"), "user", runner)
        self.assertEqual(client.update_doc("doccn1", 12, "# 更新"), 13)
        self.assertIn("--revision-id", runner.calls[-1])
        self.assertIn("12", runner.calls[-1])

    def test_conflict_response_becomes_revision_conflict(self):
        client = LarkCli(Path("lark-cli"), "user", FakeRunner(revision_conflict()))
        with self.assertRaises(RevisionConflict):
            client.update_doc("doccn1", 12, "# 更新")
~~~

- [ ] **Step 6: Implement the adapter.**

~~~python
class LarkCli:
    def update_doc(self, doc_token: str, revision_id: int, content: str) -> int:
        result = self._json(
            "docs", "+update", "--as", self.identity, "--doc", doc_token,
            "--command", "overwrite", "--doc-format", "markdown",
            "--revision-id", str(revision_id), "--content", content, "--format", "json",
        )
        return int(result["data"]["document"]["revision_id"])
~~~

Use subprocess.run with UTF-8 decoding and check=False. Parse the final JSON payload, redact token-like values from errors, and distinguish revision conflict, authentication, permission, not-found, rate-limit, and transient failures. preflight runs auth status --json --verify, then wiki +node-get for the configured target root and the configured source root, and makes no mutation.

 
- [ ] **Step 7: Run both test modules.**

Run: python -m unittest plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_config.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_lark_cli.py -v  
Expected: PASS.

- [ ] **Step 8: Commit.**

~~~powershell
git add plugins/picturebook-screenwriter/config/feishu-knowledge-base.example.json plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/models.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/config.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/lark_cli.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_config.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_lark_cli.py
git commit -m "feat: add Feishu knowledge configuration and CLI"
~~~

### Task 2: Implement page metadata, remote index, and lease

**Files:**
- Create: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/page_codec.py
- Create: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/control_plane.py
- Test: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_page_codec.py
- Test: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_control_plane.py

**Interfaces:**
- Produces parse_candidate(markdown), logical_key(page), render_remote_page(body, metadata), parse_remote_page(markdown).
- Produces ControlPlane.read_index(), acquire_lock(holder, now), refresh_lock(lease, now), release_lock(lease), rebuild_index(pages).
- Publisher and loader use these interfaces only.

- [ ] **Step 1: Write failing page codec tests.**

~~~python
def test_remote_page_round_trip_excludes_system_metadata(self):
    text = render_remote_page(
        "# 小老鼠世界观\n\n正文。\n",
        {"key": "海外绘本/小老鼠迈尔斯/worldview", "page_type": "worldview",
         "source_node_tokens": ["K8EXw4Ja2i7mGnk1Tvgc4zcknkd"], "last_ai_revision_id": 12},
    )
    page = parse_remote_page(text)
    self.assertEqual(page.body, "# 小老鼠世界观\n\n正文。\n")
    self.assertEqual(page.metadata["key"], "海外绘本/小老鼠迈尔斯/worldview")

def test_metadata_must_be_final_section(self):
    broken = "## 系统元数据（请勿编辑）\n" + chr(96) * 3 + "json\n{}\n" + chr(96) * 3 + "\n\n正文"
    with self.assertRaisesRegex(PageCodecError, "final section"):
        parse_remote_page(broken)
~~~

- [ ] **Step 2: Run tests to verify failure.**

Run: python -m unittest plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_page_codec.py -v  
Expected: FAIL because page_codec.py does not exist.

- [ ] **Step 3: Implement the reader/system envelope.**

A candidate retains YAML frontmatter only in task-local wiki_staging. Publishing removes it from reader content and appends one final section named 系统元数据（请勿编辑） containing canonical JSON. The logical key is series_id/project_id/page_type. Reject missing/duplicate/final-position violations, invalid page types, changed keys, and invalid source token lists. Preserve all existing machine-data YAML blocks in the reader body.

- [ ] **Step 4: Write failing remote lease tests.**

~~~python
def test_first_writer_acquires_lock_at_revision(self):
    plane = ControlPlane(FakeCli(lock_revision=7, lock_content=empty_lock()), control_tokens())
    lease = plane.acquire_lock("alice@host", FIXED_NOW)
    self.assertEqual(lease.holder, "alice@host")
    with self.assertRaises(LockHeld):
        plane.acquire_lock("bob@host", FIXED_NOW)

def test_bad_index_stops_writes(self):
    plane = ControlPlane(FakeCli(index_content="# AI_KB_INDEX_V1\nnot-json"), control_tokens())
    with self.assertRaises(ControlPlaneCorrupt):
        plane.read_index()
~~~

- [ ] **Step 5: Implement remote control documents and CAS lease.**

同步索引 begins with # AI_KB_INDEX_V1 and contains exactly one JSON object. 同步锁 begins with # AI_KB_LOCK_V1 and contains exactly one object with schema_version, run_id, holder, started_at, and expires_at.

Acquire fetches the lock, verifies expiry, and calls update_doc with the fetched revision. On RevisionConflict it refetches once; if the new lock is non-expired it raises LockHeld. Refresh and release verify both run_id and holder. Validate every index entry and token before a publish. A malformed index stops writes; rebuild_index scans published page metadata and rejects duplicate logical keys.

- [ ] **Step 6: Run Task 2 tests.**

Run: python -m unittest plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_page_codec.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_control_plane.py -v  
Expected: PASS.

- [ ] **Step 7: Commit.**

~~~powershell
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/page_codec.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/control_plane.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_page_codec.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_control_plane.py
git commit -m "feat: add authoritative page and control protocols"
~~~

### Task 3: Port wiki-ingest as a candidate-only pipeline

**Files:**
- Create: plugins/picturebook-screenwriter/skills/wiki-ingest/SKILL.md
- Create: plugins/picturebook-screenwriter/skills/wiki-ingest/references/structured-output-templates.md
- Create: plugins/picturebook-screenwriter/skills/wiki-ingest/references/feishu-wiki-extraction.md
- Create: plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/check_delta.py
- Create: plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/generate_entries.py
- Create: plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/scan_external_links.py
- Create: plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/run_capture.py
- Create: matching test_check_delta.py, test_generate_entries.py, test_scan_external_links.py, test_run_capture.py
- Do not copy: write_library.py, sync_state.py, lint_mechanical.py, retire_duplicates.py, and their tests.

**Interfaces:**
- Produces validated candidate Markdown and wiki_staging/_manifest.json.
- Every manifest record includes logical key and source revision vector. No ingestion script writes target Wiki pages.

- [ ] **Step 1: Copy generic source code.**

Copy only the listed generic files and templates from E:\picturebook-screenwriter\workbuddy-expert\skills\wiki-ingest. Do not copy caches, credentials, manifests, or WorkBuddy scripts.

- [ ] **Step 2: Run the pre-port tests.**

Run: python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest/scripts -p "test_*.py" -v  
Expected: FAIL because generate_entries.py imports write_library.canonical_title.

- [ ] **Step 3: Write the failing duplicate-key test.**

~~~python
def test_display_titles_cannot_share_logical_key(self):
    first = write_candidate(self.tmp, "a.md", title="世界设定", page_type="worldview")
    second = write_candidate(self.tmp, "b.md", title="另一个世界设定", page_type="worldview")
    issues = check_cross_file_dups([first, second])
    self.assertTrue(any(level == "FAIL" and "logical key" in reason for level, reason in issues))
~~~

- [ ] **Step 4: Replace the WorkBuddy title dependency and rewrite the skill contract.**

~~~python
def candidate_key(frontmatter: dict[str, object]) -> str:
    return "/".join((
        str(frontmatter.get("series_id", "")),
        str(frontmatter.get("project_id", "")),
        str(frontmatter["page_type"]),
    ))
~~~

Use candidate_key for duplicate detection. Retain source citations, XLSX preservation, and machine-data checks. SKILL.md reads the configured source Wiki, writes only task-local candidates, validates them, and hands the manifest to feishu-knowledge-store. Remove all WorkBuddy API, data table, sync state table, and write_library instructions.

Extend candidate frontmatter with required source_node_tokens and source_revision_parts fields. Change build_manifest to retain its existing root/series tree and add one deterministic flat entries list for the sync adapter. Each flat entry must include path, key, page_type, series_id, project_id, and source_revisions, where source_revisions is a token-to-revision dictionary built by pairing those two fields. Reject unequal list lengths, duplicate source node tokens, missing source token values, and blank revision parts. Add this passing test before marking the task complete:

~~~python
def test_manifest_contains_source_revision_vector(self):
    candidate = write_candidate(self.tmp, "worldview.md", page_type="worldview",
        source_node_tokens=["node-a", "node-b"], source_revision_parts=["17", "28"])
    manifest = build_manifest(str(self.tmp), {candidate: []})
    entry = manifest["entries"][0]
    self.assertEqual(entry["source_revisions"], {"node-a": "17", "node-b": "28"})
~~~

- [ ] **Step 5: Run portable-ingestion validation.**

Run: python -m unittest discover -s plugins/picturebook-screenwriter/skills/wiki-ingest/scripts -p "test_*.py" -v  
Expected: PASS.

Run: rg -n -i "write_library|sync_state|workbuddy.*api|资料库 API" plugins/picturebook-screenwriter/skills/wiki-ingest  
Expected: no matches.

- [ ] **Step 6: Commit.**

~~~powershell
git add plugins/picturebook-screenwriter/skills/wiki-ingest
git commit -m "feat: add portable wiki ingest candidates"
~~~

### Task 4: Add docx publishing and human-priority sync

**Files:**
- Create: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py
- Create: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/merge_protocol.py
- Create: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_knowledge.py
- Create: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/SKILL.md
- Test: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py
- Test: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_merge_protocol.py
- Test: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_knowledge.py

**Interfaces:**
- Produces Publisher.initialize(), publish_new(candidate), conditional_update(entry, current, merged_markdown, source_revisions), append_conflict(record).
- Produces SyncService.prepare() and SyncService.apply().
- The agent may author a MergeDecision, but Python validates every decision and owns every mutation.

- [ ] **Step 1: Write failing publisher safety tests.**

~~~python
def test_initialize_creates_system_tree_only_once(self):
    self.publisher.initialize()
    self.assertEqual(self.cli.created_titles, [
        "00_使用说明", "AI知识库编辑说明", "01_知识内容",
        "02_导航与日志", "知识导航索引", "同步日志",
        "99_系统控制台", "同步索引", "同步锁", "冲突待处理",
    ])
    self.publisher.initialize()
    self.assertEqual(len(self.cli.created_titles), 10)

def test_revision_conflict_does_not_replace_human_page(self):
    self.cli.update_error = RevisionConflict("changed")
    with self.assertRaises(RevisionConflict):
        self.publisher.conditional_update(entry(), current_human_page(), "# 新候选", {"source": "19"})
    self.assertEqual(self.cli.read(entry().doc_token), current_human_page().content)
~~~

- [ ] **Step 2: Run publisher test to verify failure.**

Run: python -m unittest plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py -v  
Expected: FAIL because publisher.py does not exist.

- [ ] **Step 3: Implement idempotent docx tree and conditional updates.**

Every navigation container is a docx page, because the Wiki CLI creates docx nodes rather than folders. Find children by exact title plus parent token. Create leaf pages with docs +create --parent-token <parent node> --title <title> --doc-format markdown. Fetch and verify final metadata before adding an index record.

conditional_update refuses has_non_roundtrippable_content, then calls update_doc using current.revision_id. It propagates RevisionConflict without a hidden retry. Regenerate navigation from the remote index. Sync log is append-only. Conflict queue records logical key, source links, current document link, Chinese reason, timestamp, and current revision.

`has_non_roundtrippable_content` is computed by `page_codec.parse_remote_page`, not guessed from the request. It marks conservative resource and comment indications at [page_codec.py](E:\codex-picturebook-agent\.worktrees\codex-feishu-authoritative-knowledge\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\page_codec.py:152). Any unknown block/markdown marker that cannot be safely round-tripped still produces `needs_review`, never a publish.

- [ ] **Step 4: Write failing merge tests.**

~~~python
def test_source_only_change_can_publish(self):
    result = classify(base="# A", current="# A", candidate="# B", source_changed=True)
    self.assertEqual(result.required_action, "publish")

def test_human_and_source_change_requires_agent_decision(self):
    result = classify(base="# A", current="# 人工规则", candidate="# 新源规则", source_changed=True)
    self.assertEqual(result.required_action, "agent_decision")

def test_publish_that_drops_human_line_is_rejected(self):
    with self.assertRaisesRegex(MergeValidationError, "人工"):
        validate_decision(requirement(), "# A", "# 人工规则", publish("# 新源规则"))

def test_publish_that_reintroduces_human_deleted_content_is_rejected(self):
    with self.assertRaisesRegex(MergeValidationError, "人工"):
        validate_decision(requirement(), "# A\n\n旧设定\n", "# A\n", "# A\n\n人工补充\n")
~~~

- [ ] **Step 5: Implement merge protocol and phase-separated sync command.**

~~~python
def classify(base: str, current: str, candidate: str, source_changed: bool) -> MergeRequirement:
    human_changed = current != base
    if not source_changed:
        return MergeRequirement("preserve", "source_unchanged")
    if not human_changed:
        return MergeRequirement("publish", "source_only_change")
    if candidate == base:
        return MergeRequirement("preserve", "candidate_unchanged")
    return MergeRequirement("agent_decision", "human_and_source_changed")
~~~

The skill requires publish only when `merge_protocol` proves a full three-way, line-level reconciliation: every human insertion stays inserted, every human deletion stays deleted, every surviving source change is accepted, and the result still contains the system metadata block. Otherwise it outputs queue. The validator rejects publish if it changes key, drops or reverts any human addition or deletion, omits final metadata, or drifts from both current and candidate.

Implement:

~~~text
sync_knowledge.py preflight --config <path>
sync_knowledge.py initialize --config <path>
sync_knowledge.py prepare --config <path> --staging-dir <dir> --out <work-items.json>
sync_knowledge.py validate-decisions --work-items <work-items.json> --decisions <decisions.json>
sync_knowledge.py apply --config <path> --work-items <work-items.json> --decisions <decisions.json>
~~~

prepare acquires the remote lease then, for every existing entry, calls fetch_doc_revision(entry.doc_token, entry.last_ai_revision_id) for base and fetch_doc(entry.doc_token) for current before storing base/current/candidate in the task-local bundle. If the historical base is unavailable, it queues that entry rather than guessing a baseline. apply verifies the lease, refetches each page, permits exactly one fetch/merge retry after RevisionConflict, queues unsupported or undecidable pages, releases the lease in finally, and reports published/preserved/queued/failed/retried counts.

- [ ] **Step 6: Add orchestration tests.**

~~~python
def test_queue_preserves_human_page(self):
    report = self.service.apply([queue_decision("s/p/worldview")])
    self.assertEqual(report.queued, 1)
    self.assertEqual(self.cli.read("doc-worldview"), "# 人工设定")

def test_second_conflict_queues_after_one_retry(self):
    self.cli.conflicts_before_success = 2
    report = self.service.apply([publish_decision("s/p/worldview")])
    self.assertEqual(report.retried, 1)
    self.assertEqual(report.queued, 1)
~~~

 
- [ ] **Step 7: Run the Task 4 test suite.**

Run: python -m unittest plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_merge_protocol.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_knowledge.py -v  
Expected: PASS.

- [ ] **Step 8: Commit.**

~~~powershell
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store
git commit -m "feat: add human-priority Feishu knowledge sync"
~~~

### Task 5: Load authoritative knowledge and update the plugin workflow

**Files:**
- Create: plugins/picturebook-screenwriter/skills/knowledge-loader/SKILL.md
- Create: plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/cache.py
- Create: plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/load_knowledge.py
- Test: plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/test_load_knowledge.py
- Create: plugins/picturebook-screenwriter/tests/test_skill_contract.py
- Modify: plugins/picturebook-screenwriter/skills/picturebook-screenwriter/SKILL.md
- Modify: plugins/picturebook-screenwriter/.codex-plugin/plugin.json
- Modify: plugins/picturebook-screenwriter/README.md
- Modify: README.md

**Interfaces:**
- Produces load(query) -> KnowledgeEvidenceBundle with key, doc_token, revision_id, title, reader body, and Chinese warnings.
- Produces a per-user, timestamped offline cache that `load(..., allow_offline_cache=True)` may use only when the target Wiki is unreachable.
- Saved writing artifacts include knowledge_provenance for every source used.

- [ ] **Step 1: Write failing loader and entry contract tests.**

~~~python
def test_loader_reads_remote_page_and_strips_metadata(self):
    bundle = self.loader.load(KnowledgeQuery(project_id="小老鼠迈尔斯", terms=("角色", "铃铛")))
    self.assertEqual(bundle.items[0].key, "海外绘本/小老鼠迈尔斯/characters")
    self.assertEqual(bundle.items[0].revision_id, 45)
    self.assertNotIn("系统元数据", bundle.items[0].content)

def test_entry_routes_sync_to_store(self):
    text = ENTRY_SKILL.read_text(encoding="utf-8")
    self.assertIn("feishu-knowledge-store", text)
    self.assertNotIn("If the user asks for Feishu sync", text)

def test_offline_cache_marks_evidence_and_warns(self):
    bundle = self.loader.load(KnowledgeQuery(project_id="小老鼠迈尔斯"), allow_offline_cache=True)
    self.assertTrue(bundle.offline)
    self.assertIn("最后确认的本地缓存", bundle.warnings[0])
    self.assertEqual(bundle.fetched_at, "2026-09-17T10:00:00+08:00")
~~~

- [ ] **Step 2: Run tests to prove failure.**

Run: python -m unittest plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/test_load_knowledge.py plugins/picturebook-screenwriter/tests/test_skill_contract.py -v  
Expected: FAIL because loader and integration are absent.

- [ ] **Step 3: Implement read-only retrieval.**

Filter remote index entries by project/page type before fetching docx. Remove system metadata through parse_remote_page, score exact Chinese query terms, sort by score descending then key, and cap default output at eight pages. Include needs_review pages but warn in Chinese with title and conflict link. Never treat local staging as a knowledge source.

After a successful remote load, write the returned bundle to `<workspace>/.picturebook-screenwriter/cache/<user>/knowledge-bundle.json` together with `fetched_at` and each source revision. On target-Wiki failure, `load(query, allow_offline_cache=True)` may read this cache, but the returned bundle must be marked `offline=true` and the summary must tell the user that it is from the last confirmed cache, not from the authoritative Wiki. Remote reads remain the only source of truth; cache is per-user and non-authoritative.

- [ ] **Step 4: Integrate skill routing and documentation.**

Route synchronization to wiki-ingest then prepare/validate/apply. Creation and revision load text-craft plus knowledge-loader before drafting. Preserve the existing confirmation gate. Approved saved artifacts add knowledge_provenance containing key, doc_token, revision_id.

Update plugin descriptions to say 多人协作飞书知识库同步与权威检索. Document personal lark-cli auth login, source/target ACLs, config copying, human-priority behavior, and recovery for lock held, bad index, and a local CLI token-store lock.

- [ ] **Step 5: Run the loader and entry contract tests.**

Run: python -m unittest plugins/picturebook-screenwriter/skills/knowledge-loader/scripts/test_load_knowledge.py plugins/picturebook-screenwriter/tests/test_skill_contract.py -v  
Expected: PASS.

- [ ] **Step 6: Validate the plugin manifest.**

Run: python C:\Users\lvan\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py .\plugins\picturebook-screenwriter  
Expected: validation succeeds.

- [ ] **Step 7: Commit.**

~~~powershell
git add README.md plugins/picturebook-screenwriter
git commit -m "feat: integrate authoritative knowledge into writing"
~~~

### Task 6: Verify end-to-end safety before release

**Files:**
- Create: plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_end_to_end.py
- Modify: README.md

**Interfaces:**
- Exercises all public interfaces through an in-memory fake CLI; it never mutates the production Wiki.
- Documents a non-mutating live preflight command.

- [ ] **Step 1: Write the full offline scenario.**

~~~python
def test_two_sync_users_and_human_edit_never_lose_human_content(self):
    system = build_fake_system()
    system.initialize()
    system.apply_candidate("s/p/worldview", "# AI v1")
    system.human_edit("s/p/worldview", "# 人工世界观")
    report = system.sync_source_change("s/p/worldview", "# AI v2")
    self.assertEqual(system.read("s/p/worldview"), "# 人工世界观")
    self.assertEqual(report.queued, 1)
    self.assertTrue(system.second_user_attempt().lock_held)
~~~

Also cover source-only update of one page, zero writes when unchanged, corrupted-index stop, resource-bearing page queue, single retry after conflict, and loader revision citations.

- [ ] **Step 2: Run all checks.**

Run: python -m unittest discover -s plugins/picturebook-screenwriter -p "test_*.py" -v  
Expected: PASS.

Run: node .\plugins\picturebook-screenwriter\skills\staging-planner\scripts\run_regression.js  
Expected: existing regression suite passes.

Run: python -m unittest discover -s .\plugins\picturebook-screenwriter\skills\craft-benchmark-check\scripts -p "test_*.py" -v  
Expected: existing craft benchmark tests pass.

- [ ] **Step 3: Add the live preflight instructions.**

~~~powershell
$env:PICTUREBOOK_KB_CONFIG = "$PWD\.picturebook-screenwriter\feishu-knowledge-base.json"
lark-cli auth login
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\sync_knowledge.py preflight --config $env:PICTUREBOOK_KB_CONFIG
~~~

Expected: identity verification, source access, target root resolution, and target space_id; no Wiki node or page is created.

- [ ] **Step 4: Validate the plugin and diff.**

Run: python C:\Users\lvan\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py .\plugins\picturebook-screenwriter  
Expected: validation succeeds.

Run: git diff --check  
Expected: no whitespace errors.

- [ ] **Step 5: Commit.**

~~~powershell
git add README.md plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_end_to_end.py
git commit -m "test: cover authoritative knowledge workflow"
~~~

## Plan self-review

| Spec requirement | Tasks |
| --- | --- |
| Target Wiki is sole shared authority | 1, 2, 4, 5, 6 |
| Source Wiki is read-only input | 1, 3, 4, 5 |
| Native docx with revision preconditions | 1, 2, 4, 6 |
| Human edits are authoritative | 2, 4, 5, 6 |
| Remote index, lock, recovery, queue | 2, 4, 6 |
| No WorkBuddy library dependency | 3, 5, 6 |
| Portable multi-user CLI access | 1, 5, 6 |
| Retrieval and writing provenance | 5, 6 |
| Offline cache fallback and clear user warning | 5, 6 |
| Initialization, logs, and failure behavior | 2, 4, 6 |

The plan assigns every design requirement. It deliberately excludes source-Wiki writes, WorkBuddy integration, local shared state, automatic semantic conflict resolution, and destructive recovery actions.
