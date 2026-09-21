# Feishu P0 Sync Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the real publishing loop: locate the actual control pages, publish knowledge pages with correct titles and tokens, and write successful pages into the remote sync index.

**Architecture:** Keep `Publisher` responsible for one page and `ControlPlane` responsible for the index and lock. `SyncRunner` will orchestrate candidates under the `01_知识内容` token returned by `Publisher.initialize()`, consume corrected `IndexEntry` values, and immediately update the shared index. `SyncService` will use per-decision index entries instead of a fixed test entry.

**Tech Stack:** Python 3.12+ standard library, `unittest`, existing `LarkCli` adapter, Fake CLI doubles; no new dependencies.

**Spec:** `docs/workbuddy-feishu-knowledge-base-compatibility.md` and `docs/plans/2026-09-20-致WorkBuddy侧-知识内容结构确认答复.md`

## Global Constraints

- Remote target page display title is the full logical key: `{series_id}/{project_id}/{page_type}`.
- `01_知识内容` is the only parent for knowledge pages; no series or project containers are created.
- Control page node titles are exactly `AI_KB_INDEX_V1`, `AI_KB_LOCK_V1`, and `AI_KB_CONFLICT_QUEUE_V1`.
- All remote mutations happen while holding a valid remote lease.
- Document reads use `docs +fetch`; supported CLI versions are only `1.0.95` and `1.0.96`.
- Tests must not touch Feishu; all network behavior is exercised through injected `FakeCli` objects.

## Review Focus

- Duplicate control pages: `initialize()` must fail closed instead of choosing one. Test: Task 1.
- Wrong create title or identity: `publish_new()` must create by logical key and return real document/node tokens. Test: Task 2.
- Stale or malformed index: `update_index()` must verify returned revision and parsed entries. Test: Task 4.
- Wrong parent or discarded publish result: `SyncRunner` must publish under `tokens["content"]` and index the returned entry. Test: Task 5.
- Per-decision identity loss: `SyncService` must not reuse one test entry for every key. Test: Task 6.

---

### Task 1: Align Control Page Titles

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py:15-19`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py:88-96`

**Interfaces:**
- Consumes: existing `Publisher.initialize()`.
- Produces: `Publisher.tokens` keys `"index"`, `"lock"`, `"conflict"` mapped to the actual control page tokens.

- [ ] **Step 1: Write the failing test**

```python
def test_initialize_uses_actual_control_page_titles(self):
    tokens = self.publisher.initialize()
    self.assertEqual(self.cli.created_titles, [
        "00_使用说明", "AI知识库编辑说明", "01_知识内容",
        "02_导航与日志", "知识导航索引", "同步日志",
        "99_系统控制台", "AI_KB_INDEX_V1", "AI_KB_LOCK_V1",
        "AI_KB_CONFLICT_QUEUE_V1",
    ])
    self.assertEqual(tokens["index"], "node-8")
    self.assertEqual(tokens["lock"], "node-9")
    self.assertEqual(tokens["conflict"], "node-10")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest test_publisher.PublisherTests.test_initialize_uses_actual_control_page_titles -v`
Expected: FAIL because the old titles are `同步索引`, `同步锁`, and `冲突待处理`.

- [ ] **Step 3: Implement title constants**

```python
CONTROL_TREE = (
    ("00_使用说明", ("AI知识库编辑说明",)),
    ("02_导航与日志", ("知识导航索引", "同步日志")),
    ("99_系统控制台", (
        "AI_KB_INDEX_V1",
        "AI_KB_LOCK_V1",
        "AI_KB_CONFLICT_QUEUE_V1",
    )),
)
```

In `initialize()`, after creating each child, map canonical names:

```python
canonical = {
    "AI_KB_INDEX_V1": "index",
    "AI_KB_LOCK_V1": "lock",
    "AI_KB_CONFLICT_QUEUE_V1": "conflict",
}
if child in canonical:
    tokens[canonical[child]] = child_token
```

- [ ] **Step 4: Run publisher tests**

Run: `python -m unittest test_publisher -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py
git commit -m "fix(feishu): locate control pages by actual titles"
```

### Task 2: Publish New Pages By Logical Key And Return Real Identity

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py:57-76`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py`

**Interfaces:**
- Consumes: `IndexEntry.key`.
- Produces: `publish_new(entry: IndexEntry, body: str, parent: str) -> IndexEntry`; the returned entry has real `doc_token`, `wiki_node_token`, and corrected revisions.

- [ ] **Step 1: Write the failing test**

```python
def test_publish_new_uses_logical_key_title_and_returns_real_tokens(self):
    self.cli = FakeCli()
    self.publisher = Publisher(self.cli, "root")
    result = self.publisher.publish_new(entry(), "# 正文", "content-root")
    self.assertEqual(self.cli.created_titles, ["s/p/worldview"])
    self.assertEqual(result.doc_token, "doc-1")
    self.assertEqual(result.wiki_node_token, "node-1")
    self.assertEqual(result.last_ai_revision_id, 5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest test_publisher.PublisherTests.test_publish_new_uses_logical_key_title_and_returns_real_tokens -v`
Expected: FAIL because the current create title is `entry.doc_token`.

- [ ] **Step 3: Implement title and identity handling**

```python
def publish_new(self, entry: IndexEntry, body: str, parent: str) -> IndexEntry:
    title = entry.key
    page = render_remote_page(body, self._metadata(entry, revision=0))
    result = self.cli.create_doc(parent, title, page)
    document = result.get("data", {}).get("document", {})
    revision = int(document.get("revision_id", 0))
    if revision <= 0:
        raise NeedsReview("created document did not return a positive revision")
    doc_token = document.get("document_id", document.get("doc_token", document.get("token")))
    if not doc_token:
        raise NeedsReview("created document did not return a usable doc token")
    node_token = self._node_token(parent, title)
    current = self.cli.fetch_doc(doc_token).get("data", {}).get("document", {})
    parsed = parse_remote_page(current["content"])
    if parsed.metadata["key"] != entry.key:
        raise NeedsReview("created page metadata key mismatch")
    fixed = self._write_page_with_metadata_fix(
        doc_token, int(current["revision_id"]), parsed.body, entry,
    )
    return replace(fixed, doc_token=doc_token, wiki_node_token=node_token)
```

Add a focused node lookup helper:

```python
def _node_token(self, parent: str, title: str) -> str:
    matches = [node for node in self.cli.list_nodes(parent) if node.get("title") == title]
    if len(matches) != 1:
        raise NeedsReview(f"expected exactly one page named {title}")
    for key in ("node_token", "obj_token", "token"):
        if matches[0].get(key):
            return matches[0][key]
    raise NeedsReview(f"created page has no usable node token: {title}")
```

- [ ] **Step 4: Run publisher tests**

Run: `python -m unittest test_publisher -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py
git commit -m "fix(feishu): key created pages by logical identity"
```

### Task 3: Expose A Real Current-Page Reader

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_knowledge.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py`

**Interfaces:**
- Produces: `Publisher.fetch_current(doc_token: str) -> dict[str, Any]` returning `{"revision_id": int, "content": str}`.
- Consumes: existing `LarkCli.fetch_doc()`.

- [ ] **Step 1: Write the failing test**

```python
def test_fetch_current_returns_validated_revision_and_content(self):
    current = page("# 正文")
    self.cli.docs[entry().doc_token] = {"revision_id": 9, "content": current}
    self.assertEqual(
        self.publisher.fetch_current(entry().doc_token),
        {"revision_id": 9, "content": current},
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest test_publisher.PublisherTests.test_fetch_current_returns_validated_revision_and_content -v`
Expected: FAIL with `AttributeError: ... fetch_current`.

- [ ] **Step 3: Implement the reader**

```python
def fetch_current(self, doc_token: str) -> dict[str, Any]:
    document = self.cli.fetch_doc(doc_token).get("data", {}).get("document", {})
    revision = document.get("revision_id")
    content = document.get("content")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise NeedsReview(f"current page has invalid revision: {doc_token}")
    if not isinstance(content, str) or not content:
        raise NeedsReview(f"current page is empty or unreadable: {doc_token}")
    return {"revision_id": revision, "content": content}
```

- [ ] **Step 4: Replace the test-only `_current()` in `SyncService`**

Change `_current()` to:

```python
def _current(self, doc_token: str) -> Mapping[str, object]:
    return self.publisher.fetch_current(doc_token)
```

- [ ] **Step 5: Run tests**

Run: `python -m unittest test_publisher test_sync_knowledge test_end_to_end -v`
Expected: PASS after updating fake publishers to provide `fetch_current`.

- [ ] **Step 6: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_knowledge.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_knowledge.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_end_to_end.py
git commit -m "feat(feishu): read current pages through publisher boundary"
```

### Task 4: Add Verified Index Upserts

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/control_plane.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_control_plane.py`

**Interfaces:**
- Produces: `ControlPlane.update_index(entries: Iterable[IndexEntry]) -> dict[str, IndexEntry]`.
- Consumes: `_parse_control()`, `_render_control()`, `_fetch()`, and `_revision_from_update_result()`.

- [ ] **Step 1: Write the failing success test**

```python
def test_update_index_merges_entries_and_verifies_readback(self):
    cli = FakeCli()
    plane = ControlPlane(cli, control_tokens())
    updated = IndexEntry(
        key="s/p/worldview", doc_token="doc-world", wiki_node_token="node-world",
        source_revisions={"source": "r2"}, last_ai_revision_id=9,
        last_seen_revision_id=9, status="published",
    )
    result = plane.update_index([updated])
    self.assertEqual(result["s/p/worldview"], updated)
    self.assertEqual(cli.docs["index-doc"]["revision_id"], 3)
```

- [ ] **Step 2: Write the failing race test**

```python
def test_update_index_detects_postwrite_index_change(self):
    class RacyIndexCli(FakeCli):
        def update_doc(self, token, revision, content):
            result = super().update_doc(token, revision, content)
            self.docs[token]["content"] = index_content()
            return result

    plane = ControlPlane(RacyIndexCli(), control_tokens())
    with self.assertRaises(ControlPlaneCorrupt):
        plane.update_index([IndexEntry(
            key="s/p/worldview", doc_token="doc-world", wiki_node_token="node-world",
            source_revisions={"source": "r2"}, last_ai_revision_id=9,
            last_seen_revision_id=9, status="published",
        )])
```

- [ ] **Step 3: Run both tests to verify they fail**

Run: `python -m unittest test_control_plane.ControlPlaneTests.test_update_index_merges_entries_and_verifies_readback test_control_plane.ControlPlaneTests.test_update_index_detects_postwrite_index_change -v`
Expected: FAIL with `AttributeError: ... update_index`.

- [ ] **Step 4: Implement index rendering and update**

```python
def update_index(self, entries: Iterable[IndexEntry]) -> dict[str, IndexEntry]:
    merged = self.read_index()
    for entry in entries:
        merged[entry.key] = entry
    payload = {
        "schema_version": 1,
        "entries": [asdict(entry) for entry in sorted(merged.values(), key=lambda item: item.key)],
    }
    rendered = _render_control("# AI_KB_INDEX_V1", payload)
    revision, _ = self._fetch(self.control_tokens["index"])
    result = self.cli.update_doc(self.control_tokens["index"], revision, rendered)
    if result.get("warnings") or result.get("data", {}).get("result") == "partial_success":
        raise ControlPlaneCorrupt("index update returned warnings or partial success")
    new_revision = self._revision_from_update_result(result)
    verified_revision, verified_content = self._fetch(self.control_tokens["index"])
    verified = _parse_control(verified_content, "# AI_KB_INDEX_V1")
    if verified_revision != new_revision or verified != payload:
        raise ControlPlaneCorrupt("index readback did not match the expected entries")
    return self.read_index()
```

Add `asdict` to the existing `dataclasses` import.

- [ ] **Step 5: Run control-plane tests**

Run: `python -m unittest test_control_plane -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/control_plane.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_control_plane.py
git commit -m "feat(feishu): add verified sync index upserts"
```

### Task 5: Drive SyncRunner Through Content Root And Index

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_runner.py:66-107`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_runner.py:160-176`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_runner.py`

**Interfaces:**
- Consumes: `Publisher.initialize()`, `Publisher.publish_new()`, `ControlPlane.update_index()`.
- Produces: `SyncReport.errors: list[str]` and a remote index containing every successfully published entry.

- [ ] **Step 1: Write the failing orchestration test**

```python
def test_publish_uses_content_root_and_updates_index(self):
    self._write_manifest()
    plane = RecordingPlane()
    publisher = FakePublisher()
    publisher.initialize = lambda: {"content": "content-root"}
    runner = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane)
    report = runner.publish(self.run_dir)
    self.assertEqual(publisher.published[0][2], "content-root")
    self.assertEqual([entry.key for entry in plane.updated], [
        "海外绘本/小老鼠迈尔斯/worldview",
    ])
    self.assertEqual(report["published"], 1)
```

Extend `RecordingPlane` from the existing fake:

```python
class RecordingPlane(FakeControlPlane):
    def __init__(self):
        super().__init__()
        self.updated = []

    def update_index(self, entries):
        self.updated.extend(entries)
        return {entry.key: entry for entry in self.updated}
```

- [ ] **Step 2: Write the failing no-bootstrap-page test**

```python
def test_publish_does_not_create_bootstrap_page(self):
    self._write_manifest()
    publisher = FakePublisher()
    runner = SyncRunner(self.config_path, FakeCli(), publisher=publisher,
                        control_plane=FakeControlPlane())
    runner.publish(self.run_dir)
    self.assertFalse(any(key == "system/bootstrap" for key, *_ in publisher.published))
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m unittest test_sync_runner.SyncRunnerTests.test_publish_uses_content_root_and_updates_index test_sync_runner.SyncRunnerTests.test_publish_does_not_create_bootstrap_page -v`
Expected: FAIL because `_target_parent()` returns the root and `_persist_bootstrap_state()` creates a bootstrap page.

- [ ] **Step 4: Implement the publish loop**

Replace the loop body:

```python
tokens = self.publisher.initialize()
parent = tokens["content"]
published_entries = []
errors = []
for raw in entries:
    try:
        entry = self._entry(raw)
        body = self._candidate_body(run_dir, raw["path"])
        published = self.publisher.publish_new(entry, body, parent)
        self.control_plane.update_index([published])
        published_entries.append(published)
        published_count += 1
    except Exception as error:
        failed += 1
        errors.append(f"{raw.get('key', '<unknown>')}: {error}")
```

Remove `_persist_bootstrap_state()` and all calls to it. Change `_entry()` defaults to:

```python
last_ai_revision_id=0,
last_seen_revision_id=0,
```

Add `errors` to `SyncReport` and `as_dict()`.

- [ ] **Step 5: Run sync runner tests**

Run: `python -m unittest test_sync_runner -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_runner.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_runner.py
git commit -m "fix(feishu): publish candidates through content root and index"
```

### Task 6: Make SyncService Decision-Driven

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_knowledge.py:24-79`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_knowledge.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_end_to_end.py`

**Interfaces:**
- Produces: `SyncService.apply(decisions: Iterable[MergeDecision], entries: Mapping[str, IndexEntry]) -> SyncReport`.
- Consumes: `Publisher.fetch_current()`, `Publisher.conditional_update()`, `Publisher.append_conflict(parent, record)`, and `ControlPlane.update_index()`.

- [ ] **Step 1: Write the failing decision test**

```python
def test_apply_uses_matching_entry_for_each_decision(self):
    service = SyncService(self.publisher, FakePlane(), conflict_parent="conflict-doc")
    decision = MergeDecision(
        key="s/p/worldview", action="publish",
        merged_markdown=page("# 人工设定\n\n新资料"), reason="source only",
    )
    service.apply([decision], {decision.key: entry()})
    self.assertEqual(self.publisher.updates[0][0], "s/p/worldview")
```

- [ ] **Step 2: Write the failing conflict-parent test**

```python
def test_queue_sends_conflict_parent(self):
    service = SyncService(self.publisher, FakePlane(), conflict_parent="conflict-doc")
    decision = MergeDecision(key="s/p/worldview", action="queue",
                             merged_markdown=None, reason="human conflict")
    service.apply([decision], {decision.key: entry()})
    self.assertEqual(self.publisher.conflicts[0], ("conflict-doc", {
        "key": "s/p/worldview", "reason": "human conflict",
        "revision_id": 1,
    }))
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m unittest test_sync_knowledge -v`
Expected: FAIL because `SyncService` still uses `default_entry` and calls `append_conflict(record)`.

- [ ] **Step 4: Implement entry lookup and conflict signature**

```python
def __init__(self, publisher, control_plane, holder: str = "local",
             conflict_parent: str = "") -> None:
    self.publisher = publisher
    self.control_plane = control_plane
    self.holder = holder
    self.conflict_parent = conflict_parent

def apply(self, decisions, entries):
    lease = self.control_plane.acquire_lock(self.holder, datetime.now(timezone.utc))
    try:
        for decision in decisions:
            if decision.key not in entries:
                raise KeyError(f"missing index entry for decision: {decision.key}")
            entry = entries[decision.key]
            ...
            self.publisher.append_conflict(self.conflict_parent, conflict_record)
    finally:
        self.control_plane.release_lock(lease)
```

Remove `default_entry`. Keep one retry for `RevisionConflict`; on final failure append to the conflict queue.

- [ ] **Step 5: Run sync service and end-to-end tests**

Run: `python -m unittest test_sync_knowledge test_end_to_end -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_knowledge.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_knowledge.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_end_to_end.py
git commit -m "fix(feishu): drive sync service by logical key entries"
```

### Final Verification

- [ ] Run the full suite: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p "test_*.py" -v`
Expected: all tests pass.
- [ ] Run `git status --short`; expected: clean.
- [ ] Run `git log --oneline -6`; expected: one commit per task above.
