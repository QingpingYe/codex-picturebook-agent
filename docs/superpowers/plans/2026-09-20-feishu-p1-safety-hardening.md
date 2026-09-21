# Feishu P1 Safety Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Feishu writes fail closed on ambiguity: resolve without creating, seed valid control documents, prevent duplicate page creation, and verify every critical write by reading it back.

**Architecture:** Separate read-only control-plane discovery from explicit initialization. Add post-write readers for pages and conflict queues, strengthen lock refresh verification, and add stage-boundary lease renewal to `SyncRunner`. This plan depends on the P0 interfaces `Publisher.fetch_current()`, `ControlPlane.update_index()`, and actual control-page title tokens.

**Tech Stack:** Python 3.12+ standard library, `unittest`, existing `LarkCli`, Fake CLI doubles; no new dependencies.

**Spec:** `docs/workbuddy-feishu-knowledge-base-compatibility.md`

## Global Constraints

- Routine sync must locate control pages; only explicit initialization may create them.
- Existing empty control pages are not silently overwritten.
- Index, lock, and conflict queue writes must check `warnings`, `partial_success`, returned revision, and post-write readback.
- Conflict queue explanatory lines are allowed, but every line beginning with `[` must parse as `[logical_key] reason`.
- Lock refresh requires lease identity and readback verification.
- No test performs remote Feishu I/O.

## Review Focus

- Missing control page: resolution must fail without creating a replacement. Test: Task 1.
- Newly created control page: must contain valid heading/JSON immediately. Test: Task 2.
- Concurrent page creation: `publish_new()` must reject an existing logical-key page and verify exactly one after create. Test: Task 3.
- Post-write content drift: page updates must compare parsed body and metadata. Test: Task 4.
- Lost conflict record: queue append must compare parsed before/after records. Test: Task 5.
- Expired long-task lease: sync must refresh at stage boundaries. Test: Task 6.

---

### Task 1: Add Read-Only Control-Plane Resolution

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py`

**Interfaces:**
- Produces: `Publisher.resolve_control_plane() -> dict[str, str]` with keys `00_使用说明`, `01_知识内容`, `content`, `02_导航与日志`, `99_系统控制台`, `index`, `lock`, `conflict`.
- Consumes: existing `cli.list_nodes()`.

- [ ] **Step 1: Write the failing missing-page test**

```python
def test_resolve_control_plane_fails_without_creating_missing_pages(self):
    self.cli.nodes["root"] = [
        {"title": "00_使用说明", "node_token": "node-00"},
        {"title": "01_知识内容", "node_token": "node-01"},
        {"title": "02_导航与日志", "node_token": "node-02"},
        {"title": "99_系统控制台", "node_token": "node-99"},
    ]
    with self.assertRaises(NeedsReview):
        self.publisher.resolve_control_plane()
    self.assertEqual(self.cli.created_titles, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest test_publisher.PublisherTests.test_resolve_control_plane_fails_without_creating_missing_pages -v`
Expected: FAIL with `AttributeError: ... resolve_control_plane`.

- [ ] **Step 3: Implement resolve-only lookup**

```python
def resolve_control_plane(self) -> dict[str, str]:
    tokens = {}
    for title in TREE_ORDER:
        token = self._existing_token(self.target_root, title)
        if token is None:
            raise NeedsReview(f"missing system container: {title}")
        tokens[title] = token
    for parent_key, children in (
        ("00_使用说明", ("AI知识库编辑说明",)),
        ("02_导航与日志", ("知识导航索引", "同步日志")),
        ("99_系统控制台", ("AI_KB_INDEX_V1", "AI_KB_LOCK_V1", "AI_KB_CONFLICT_QUEUE_V1")),
    ):
        for child in children:
            token = self._existing_token(tokens[parent_key], child)
            if token is None:
                raise NeedsReview(f"missing system page: {child}")
            tokens[child] = token
    tokens["index"] = tokens["AI_KB_INDEX_V1"]
    tokens["lock"] = tokens["AI_KB_LOCK_V1"]
    tokens["conflict"] = tokens["AI_KB_CONFLICT_QUEUE_V1"]
    tokens["content"] = tokens["01_知识内容"]
    return tokens
```

Extract `_existing_token(parent, title) -> str | None` from `_find_or_create()`; it returns `None` for no match, raises `NeedsReview` for duplicate matches, and otherwise returns the first non-empty `node_token`, `obj_token`, or `token`.

- [ ] **Step 4: Run tests**

Run: `python -m unittest test_publisher -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py
git commit -m "feat(feishu): resolve control plane without creating pages"
```

### Task 2: Seed Valid Newly Created Control Pages

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/control_plane.py`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py`

**Interfaces:**
- Produces: `render_empty_index() -> str`, `render_empty_lock() -> str`, `render_empty_conflict_queue() -> str`.
- Consumes: `Publisher._find_or_create(parent, title)`.

- [ ] **Step 1: Write the failing seed test**

```python
def test_initialize_seeds_valid_control_documents(self):
    tokens = self.publisher.initialize()
    self.assertEqual(
        self.cli.docs[tokens["index"]]["content"],
        '# AI_KB_INDEX_V1\n```json\n{"entries":[],"schema_version":1}\n```\n',
    )
    self.assertIn('"expires_at":null', self.cli.docs[tokens["lock"]]["content"])
    self.assertEqual(
        self.cli.docs[tokens["conflict"]]["content"],
        "# AI_KB_CONFLICT_QUEUE_V1\n",
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest test_publisher.PublisherTests.test_initialize_seeds_valid_control_documents -v`
Expected: FAIL because created control pages currently have empty content.

- [ ] **Step 3: Add public render helpers**

In `control_plane.py`:

```python
def render_empty_index() -> str:
    return _render_control("# AI_KB_INDEX_V1", {"schema_version": 1, "entries": []})

def render_empty_lock() -> str:
    return _render_control("# AI_KB_LOCK_V1", {
        "schema_version": 1, "run_id": None, "holder": None,
        "started_at": None, "expires_at": None,
    })

def render_empty_conflict_queue() -> str:
    return "# AI_KB_CONFLICT_QUEUE_V1\n"
```

In `publisher.py`, use a title-to-content map during creation:

```python
seed_content = {
    "AI_KB_INDEX_V1": render_empty_index(),
    "AI_KB_LOCK_V1": render_empty_lock(),
    "AI_KB_CONFLICT_QUEUE_V1": render_empty_conflict_queue(),
}
response = self.cli.create_doc(parent, title, seed_content.get(title, ""))
```

- [ ] **Step 4: Run tests**

Run: `python -m unittest test_publisher test_control_plane -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/control_plane.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py
git commit -m "feat(feishu): seed valid control documents on initialize"
```

### Task 3: Guard New Page Creation Against Duplicates

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py`

**Interfaces:**
- Consumes: `Publisher.publish_new()` from P0.
- Produces: duplicate-safe creation under a held lease.

- [ ] **Step 1: Write the failing pre-create duplicate test**

```python
def test_publish_new_rejects_existing_logical_key_page(self):
    self.cli = FakeCli()
    self.cli.nodes["content-root"] = [{"title": entry().key, "node_token": "node-old"}]
    self.publisher = Publisher(self.cli, "root")
    with self.assertRaises(NeedsReview):
        self.publisher.publish_new(entry(), "# 正文", "content-root")
    self.assertEqual(self.cli.created_titles, [])
```

- [ ] **Step 2: Write the failing post-create race test**

```python
def test_publish_new_rejects_duplicate_created_by_rival(self):
    class DuplicateCreatingCli(FakeCli):
        def create_doc(self, parent, title, content=""):
            result = super().create_doc(parent, title, content)
            self.nodes[parent].append({"title": title, "node_token": "node-rival"})
            return result

    self.cli = DuplicateCreatingCli()
    self.publisher = Publisher(self.cli, "root")
    with self.assertRaises(NeedsReview):
        self.publisher.publish_new(entry(), "# 正文", "content-root")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m unittest test_publisher.PublisherTests.test_publish_new_rejects_existing_logical_key_page test_publisher.PublisherTests.test_publish_new_rejects_duplicate_created_by_rival -v`
Expected: both FAIL.

- [ ] **Step 4: Add pre/post checks**

At the start of `publish_new()`:

```python
existing = [node for node in self.cli.list_nodes(parent) if node.get("title") == title]
if existing:
    raise NeedsReview(f"logical key page already exists: {title}")
```

After `create_doc()` returns, call `_node_token(parent, title)`, which already requires exactly one match.

- [ ] **Step 5: Run publisher tests**

Run: `python -m unittest test_publisher -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py
git commit -m "fix(feishu): reject duplicate logical key creation"
```

### Task 4: Verify Page Updates By Readback

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py`

**Interfaces:**
- Consumes: `Publisher.fetch_current()`, `parse_remote_page()`.
- Produces: `conditional_update()` only returns success when fetched body and metadata match the intended write.

- [ ] **Step 1: Write the failing metadata drift test**

```python
def test_conditional_update_rejects_metadata_drift_after_write(self):
    current = {"revision_id": 1, "content": page("# 人工规则")}
    self.cli.docs[entry().doc_token] = dict(current)
    original_update = self.cli.update_doc

    def drift_update(token, revision, content):
        result = original_update(token, revision, content)
        self.cli.docs[token]["content"] = render_remote_page(
            "# 人工规则\n\n新资料", {**metadata(), "last_ai_revision_id": 99}
        )
        return result

    self.cli.update_doc = drift_update
    with self.assertRaises(NeedsReview):
        self.publisher.conditional_update(
            entry(), current, page("# 人工规则\n\n新资料"), {"source": "r2"}
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest test_publisher.PublisherTests.test_conditional_update_rejects_metadata_drift_after_write -v`
Expected: FAIL because only response revision is checked.

- [ ] **Step 3: Implement parsed readback**

After `final_revision` is found in `conditional_update()`:

```python
verified = self.fetch_current(entry.doc_token)
verified_page = parse_remote_page(verified["content"])
if verified_page.body != merged.body:
    raise NeedsReview("updated page body did not match merged content")
if verified_page.metadata["last_ai_revision_id"] != final_revision:
    raise NeedsReview("updated page metadata revision did not match write result")
if verified_page.metadata["source_revisions"] != dict(source_revisions):
    raise NeedsReview("updated page source revisions did not match merge decision")
```

- [ ] **Step 4: Run tests**

Run: `python -m unittest test_publisher -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py
git commit -m "fix(feishu): verify page updates by readback"
```

### Task 5: Verify Conflict Queue Appends By Readback

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py`

**Interfaces:**
- Produces: `_conflict_records(content: str) -> list[str]`, returning only parsed `[key] reason` lines.
- Consumes: `cli.fetch_doc()`, `cli.update_doc()`.

- [ ] **Step 1: Write the failing parser test**

```python
def test_conflict_records_ignores_framework_lines(self):
    content = "# AI_KB_CONFLICT_QUEUE_V1\n\n（当前无待处理冲突）\n[s/p/worldview] reason\n"
    self.assertEqual(
        self.publisher._conflict_records(content),
        [("s/p/worldview", "reason")],
    )
```

- [ ] **Step 2: Write the failing lost-record test**

```python
def test_append_conflict_rejects_lost_record_after_write(self):
    self.cli.docs["conflict-doc"] = {
        "revision_id": 4,
        "content": "# AI_KB_CONFLICT_QUEUE_V1\n",
    }
    original_update = self.cli.update_doc

    def drop_update(token, revision, content):
        result = original_update(token, revision, content)
        self.cli.docs[token]["content"] = "# AI_KB_CONFLICT_QUEUE_V1\n\n（当前无待处理冲突）\n"
        return result

    self.cli.update_doc = drop_update
    with self.assertRaises(NeedsReview):
        self.publisher.append_conflict(
            "conflict-doc", {"key": entry().key, "reason": "human conflict"}
        )
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m unittest test_publisher.PublisherTests.test_conflict_records_ignores_framework_lines test_publisher.PublisherTests.test_append_conflict_rejects_lost_record_after_write -v`
Expected: FAIL.

- [ ] **Step 4: Implement parsed append and readback**

```python
@staticmethod
def _conflict_records(content: str) -> list[tuple[str, str]]:
    records = []
    for line in content.splitlines():
        match = re.fullmatch(r"\[([^\]]+)\] (.+)", line.strip())
        if match:
            records.append((match.group(1), match.group(2)))
    return records
```

In `append_conflict()`, parse records before and after the write. Require:

```python
after[:len(before)] == before
after[-1] == (record["key"], record["reason"])
```

Otherwise raise `NeedsReview("conflict queue readback did not preserve records")`.

- [ ] **Step 5: Run tests**

Run: `python -m unittest test_publisher -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py
git commit -m "fix(feishu): verify conflict queue append by readback"
```

### Task 6: Verify Lock Refresh And Renew Long Syncs

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/control_plane.py:93-99`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_runner.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_control_plane.py`
- Test: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_runner.py`

**Interfaces:**
- Produces: verified `ControlPlane.refresh_lock() -> Lease`.
- Consumes: `Lease` identity fields.

- [ ] **Step 1: Write the failing refresh race test**

```python
def test_refresh_lock_detects_postwrite_owner_change(self):
    class RacyRefreshCli(FakeCli):
        def update_doc(self, token, revision, content):
            result = super().update_doc(token, revision, content)
            self.docs[token]["content"] = "# AI_KB_LOCK_V1\n```json\n" + json.dumps({
                "schema_version": 1, "run_id": "rival", "holder": "bob@host",
                "started_at": "2026-09-17T03:00:00Z",
                "expires_at": "2026-09-17T04:00:00Z",
            }) + "\n```\n"
            return result

    plane = ControlPlane(RacyRefreshCli(), control_tokens())
    lease = plane.acquire_lock("alice@host", FIXED_NOW)
    with self.assertRaises(LeaseOwnershipError):
        plane.refresh_lock(lease, FIXED_NOW)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest test_control_plane.ControlPlaneTests.test_refresh_lock_detects_postwrite_owner_change -v`
Expected: FAIL because refresh only parses the update result.

- [ ] **Step 3: Implement refresh readback**

After `_revision_from_update_result(result)`:

```python
verified_revision, verified_payload = self._read_lock()
self._assert_owner(verified_payload, lease)
if verified_revision != new_revision:
    raise ControlPlaneCorrupt("lock refresh revision did not match readback")
```

- [ ] **Step 4: Renew SyncRunner every five pages**

Track candidates with `enumerate(entries, 1)` and after each successful page:

```python
if processed % 5 == 0:
    lease = self.control_plane.refresh_lock(lease, datetime.now(timezone.utc))
```

In tests, use a recording control plane and a manifest with five entries; assert `refreshed == 1`.

- [ ] **Step 5: Run tests**

Run: `python -m unittest test_control_plane test_sync_runner -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/control_plane.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_runner.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_control_plane.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_runner.py
git commit -m "fix(feishu): verify lock refresh and renew long syncs"
```

### Final Verification

- [ ] Run the full suite: `python -m unittest discover -s plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts -p "test_*.py" -v`
Expected: all tests pass.
- [ ] Run `git status --short`; expected: clean.
- [ ] Run `git log --oneline -6`; expected: one commit per task above.
