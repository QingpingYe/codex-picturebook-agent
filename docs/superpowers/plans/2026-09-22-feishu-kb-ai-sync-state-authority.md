# Feishu KB-AI Sync State Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the remote `AI_KB_INDEX_V1` control document the sole authority for Feishu KB-AI target sync state while keeping source-side `first_run/new` ingestion direct.

**Architecture:** Add a pure target-action classifier, expose historical page reads through `Publisher`, and rebuild `SyncRunner.publish()` around remote-index classification. First publication remains allowed for absent keys; existing keys are validated against the remote index and page, then preserved, updated, or queued. All page writes remain conditional and must be followed by remote index update and readback.

**Tech Stack:** Python 3 standard library, `unittest`, offline fake CLI/control-plane/publisher doubles, lark-cli adapter.

**Spec:** `docs/superpowers/specs/2026-09-22-feishu-kb-ai-sync-state-authority-design.md`

## Global Constraints

- The sole shared sync-state file is `99_系统控制台/AI_KB_INDEX_V1`.
- The only target index statuses are `published`, `needs_review`, and `archived`.
- `first_run`, `new`, `changed`, `unchanged`, `deleted`, and `unknown` are source-delta verdicts, never index statuses.
- Source `first_run` and `new` nodes proceed directly into reading and candidate generation; do not add a user confirmation gate.
- Target writes must acquire the remote lease and read the validated remote index before classifying any candidate.
- Page updates require the current revision and read-after-write verification.
- Page success is incomplete unless the index update and index readback also succeed.
- Do not reconstruct a missing or corrupt index from any local file.
- Do not add `source_edit_times`, a node inclusion ledger, or automatic archive-on-source-delete.

## Review Focus

- A corrupt or missing remote index must block all target writes: pinned by Task 1.
- Source verdicts must not become target statuses: pinned by Task 1.
- Remote page metadata drift from the index must be queued, not merged blindly: pinned by Task 6.
- A target page without an index entry must be queued, not adopted: pinned by Task 6.
- A successful page write followed by an index failure must be queued: pinned by Task 6.
- Source `first_run/new` must remain direct ingestion verdicts: pinned by Task 1.

---

### Task 1: Pin Status and Source-Delta Boundaries

**Files:**

- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_control_plane.py`
- Modify: `plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/test_check_delta.py`

**Interfaces:**

- Consumes: existing `ControlPlane._index_entry()` validation and existing `check_delta.classify()`.
- Produces: regression proof that source verdicts are invalid target statuses and `first_run/new` proceed as processing verdicts.

- [ ] **Step 1: Add the index-status rejection test**

Add this test to `ControlPlaneTests` in `test_control_plane.py`:

```python
def test_index_rejects_source_delta_status_values(self):
    for status in ("first_run", "new", "changed", "unchanged", "deleted", "unknown"):
        with self.subTest(status=status):
            plane = ControlPlane(FakeCli(index_content=index_content([{
                "key": "s/p/worldview", "doc_token": "doc-world",
                "wiki_node_token": "node-world",
                "source_revisions": {"source": "1"}, "last_ai_revision_id": 1,
                "last_seen_revision_id": 1, "status": status,
            }])), control_tokens())
            with self.assertRaises(ControlPlaneCorrupt):
                plane.read_index()
```

- [ ] **Step 2: Run the rejection test**

Run: `python plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_control_plane.py -v`

Expected: all tests pass; the new test passes because `_index_entry()` already restricts status.

- [ ] **Step 3: Add the direct source-ingestion test**

Add this test to the check-delta test class in `test_check_delta.py`:

```python
def test_new_and_first_run_are_processing_verdicts_without_confirmation(self):
    first = cd.classify({"nodes": {"tokF": snap_node("tokF")}}, None)
    self.assertEqual(first["verdicts"]["tokF"]["verdict"], "first_run")
    self.assertEqual(first["summary"]["process"], 1)

    new = cd.classify(
        {"nodes": {"tokB": snap_node("tokB")}},
        {"nodes": {"tokZ": snap_node("tokZ")}},
    )
    self.assertEqual(new["verdicts"]["tokB"]["verdict"], "new")
    self.assertEqual(new["summary"]["process"], 1)
```

These calls use the existing `snap_node()` helper and remain offline.

- [ ] **Step 4: Run check-delta tests**

Run: `python plugins\picturebook-screenwriter\skills\wiki-ingest\scripts\test_check_delta.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_control_plane.py plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/test_check_delta.py
git commit -m "test: pin remote status and source delta boundary"
```

### Task 2: Expose Historical Page Reads

**Files:**

- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py`

**Interfaces:**

- Consumes: `LarkCli.fetch_doc_revision(doc_token, revision_id)`.
- Produces: `Publisher.fetch_revision(doc_token: str, revision_id: int) -> dict[str, Any]`, returning `{"revision_id": int, "content": str}`.

- [ ] **Step 1: Write failing tests**

Add these tests to `PublisherTests`:

```python
def test_fetch_revision_returns_requested_content(self):
    self.cli.fetch_doc_revision = lambda token, revision: {
        "data": {"document": {"revision_id": revision, "content": page("# 历史版本")}}
    }
    result = self.publisher.fetch_revision("doc-worldview", 4)
    self.assertEqual(result, {"revision_id": 4, "content": page("# 历史版本")})

def test_fetch_revision_rejects_invalid_revision(self):
    with self.assertRaises(NeedsReview):
        self.publisher.fetch_revision("doc-worldview", 0)
```

- [ ] **Step 2: Run tests to verify the new method is missing**

Run: `python plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_publisher.py -v`

Expected: FAIL with `AttributeError: 'Publisher' object has no attribute 'fetch_revision'`.

- [ ] **Step 3: Implement `fetch_revision`**

In `Publisher`, immediately after `fetch_current`, add:

```python
def fetch_revision(self, doc_token: str, revision_id: int) -> dict[str, Any]:
    if isinstance(revision_id, bool) or not isinstance(revision_id, int) or revision_id <= 0:
        raise NeedsReview(f"historical page has invalid revision: {doc_token}")
    document = self.cli.fetch_doc_revision(doc_token, revision_id).get("data", {}).get("document", {})
    revision = document.get("revision_id")
    content = document.get("content")
    if revision != revision_id or not isinstance(content, str) or not content:
        raise NeedsReview(f"historical page is unreadable: {doc_token}@{revision_id}")
    return {"revision_id": revision, "content": content}
```

- [ ] **Step 4: Run publisher tests**

Run: `python plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_publisher.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/publisher.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_publisher.py
git commit -m "feat: read historical knowledge pages"
```

### Task 3: Add the Pure Target-Action Classifier

**Files:**

- Create: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/target_state.py`
- Create: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_target_state.py`

**Interfaces:**

- Consumes: `IndexEntry`.
- Produces: `TargetAction(action: Literal["first_publish", "update", "preserve", "queue"], reason: str)` and `classify_target(candidate: IndexEntry, remote_index: Mapping[str, IndexEntry]) -> TargetAction`.

- [ ] **Step 1: Write failing tests**

Create `test_target_state.py`:

```python
import sys
import unittest
from dataclasses import replace
from pathlib import Path

SCRIPTS = Path(__file__).parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from models import IndexEntry
from target_state import classify_target


def entry(**changes):
    value = IndexEntry(
        key="s/p/worldview", doc_token="doc-world", wiki_node_token="node-world",
        source_revisions={"source": "r2"}, last_ai_revision_id=5,
        last_seen_revision_id=5, status="published",
    )
    return replace(value, **changes)


class TargetStateTests(unittest.TestCase):
    def test_absent_index_entry_is_first_publication(self):
        action = classify_target(entry(), {})
        self.assertEqual(action.action, "first_publish")
        self.assertEqual(action.reason, "index_entry_absent")

    def test_same_source_vector_preserves_existing_page(self):
        indexed = entry()
        candidate = entry()
        action = classify_target(candidate, {candidate.key: indexed})
        self.assertEqual(action.action, "preserve")

    def test_changed_source_vector_requests_update(self):
        indexed = entry(source_revisions={"source": "r1"})
        candidate = entry()
        action = classify_target(candidate, {candidate.key: indexed})
        self.assertEqual(action.action, "update")
        self.assertEqual(action.reason, "source_changed")

    def test_review_and_archived_entries_queue(self):
        for status in ("needs_review", "archived"):
            with self.subTest(status=status):
                indexed = entry(status=status)
                action = classify_target(entry(), {entry().key: indexed})
                self.assertEqual(action.action, "queue")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify the module is missing**

Run: `python plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_target_state.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'target_state'`.

- [ ] **Step 3: Implement the classifier**

Create `target_state.py`:

```python
"""Pure classification of a candidate against the authoritative remote index."""

from dataclasses import dataclass
from typing import Literal, Mapping

from models import IndexEntry


@dataclass(frozen=True)
class TargetAction:
    action: Literal["first_publish", "update", "preserve", "queue"]
    reason: str


def classify_target(
    candidate: IndexEntry,
    remote_index: Mapping[str, IndexEntry],
) -> TargetAction:
    indexed = remote_index.get(candidate.key)
    if indexed is None:
        return TargetAction("first_publish", "index_entry_absent")
    if indexed.status == "needs_review":
        return TargetAction("queue", "index_entry_needs_review")
    if indexed.status == "archived":
        return TargetAction("queue", "index_entry_archived")
    if dict(candidate.source_revisions) == dict(indexed.source_revisions):
        return TargetAction("preserve", "source_unchanged")
    return TargetAction("update", "source_changed")
```

- [ ] **Step 4: Run classifier tests**

Run: `python plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_target_state.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/target_state.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_target_state.py
git commit -m "feat: classify target actions from remote index"
```

### Task 4: Make SyncRunner Read the Remote Index First

**Files:**

- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_runner.py`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_runner.py`

**Interfaces:**

- Consumes: `ControlPlane.read_index() -> dict[str, IndexEntry]`; `page_codec.parse_candidate()`.
- Produces: `SyncRunner.publish()` no longer publishes before validating the remote index. Candidate bodies are parsed into `page_codec.Candidate`.

- [ ] **Step 1: Extend the fake control plane and write failing tests**

In `test_sync_runner.py`, extend `FakeControlPlane`:

```python
    def read_index(self):
        return {}
```

Add these tests to `SyncRunnerTests`:

```python
class CorruptPlane(FakeControlPlane):
    def read_index(self):
        raise ControlPlaneCorrupt("invalid index schema")


def test_publish_reads_remote_index_before_writing(self):
    self._write_manifest()
    plane = RecordingPlane()
    plane.read_index = lambda: {}
    publisher = FakePublisher()
    runner = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane)
    runner.publish(self.run_dir)
    self.assertEqual(publisher.published[0][2], "content-root")
    self.assertEqual(plane.updated[0].key, "海外绘本/小老鼠迈尔斯/worldview")

def test_corrupt_remote_index_blocks_all_writes(self):
    self._write_manifest()
    publisher = FakePublisher()
    runner = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=CorruptPlane())
    with self.assertRaises(ControlPlaneCorrupt):
        runner.publish(self.run_dir)
    self.assertEqual(publisher.published, [])
```

Add imports at the top:

```python
from control_plane import ControlPlaneCorrupt
```

- [ ] **Step 2: Run tests to verify the current behavior fails**

Run: `python plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_sync_runner.py -v`

Expected: the new tests fail because `SyncRunner.publish()` does not call `read_index()`.

- [ ] **Step 3: Implement remote-index-first publish**

In `sync_runner.py`, add imports:

```python
from control_plane import ControlPlaneCorrupt
from page_codec import parse_candidate
```

Inside `publish()`, immediately after `lease = self.control_plane.acquire_lock(...)` and before `tokens = self.publisher.initialize()`:

```python
remote_index = self.control_plane.read_index()
```

Replace `_entry(raw)` usage with a body-aware helper. Add this method:

```python
@staticmethod
def _candidate(raw: dict[str, Any], run_dir: Path):
    body = SyncRunner._candidate_body(run_dir, raw["path"])
    parsed = parse_candidate(body)
    if parsed.metadata["key"] != raw["key"]:
        raise ValueError(f"candidate logical key mismatch: {raw['key']}")
    entry = IndexEntry(
        key=raw["key"], doc_token=raw.get("doc_token", raw["key"]),
        wiki_node_token=raw.get("wiki_node_token", raw["key"]),
        source_revisions=parsed.metadata["source_revisions"],
        last_ai_revision_id=0, last_seen_revision_id=0, status="published",
    )
    return entry, parsed.body
```

In the candidate loop, replace:

```python
entry = self._entry(raw)
body = self._candidate_body(run_dir, raw["path"])
```

with:

```python
entry, body = self._candidate(raw, run_dir)
```

For this task, continue using `publisher.publish_new(entry, body, parent)` for every entry so the next task can introduce existing-key routing independently.

- [ ] **Step 4: Run sync-runner tests**

Run: `python plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_sync_runner.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_runner.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_runner.py
git commit -m "feat: validate remote index before sync writes"
```

### Task 5: Route Existing Keys Through Remote-State Decisions

**Files:**

- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_runner.py`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_runner.py`

**Interfaces:**

- Consumes: `classify_target()`, `merge_protocol.classify()`, `Publisher.fetch_current()`, `Publisher.fetch_revision()`, `Publisher.conditional_update()`, and `page_codec.parse_remote_page()/render_remote_page()`.
- Produces: `SyncRunner.publish()` handles absent keys as first publication and existing keys as `update`, `preserve`, or queue.

- [ ] **Step 1: Write failing tests for first publication, preserve, and update**

Create an indexed fake plane and richer publisher fakes in `test_sync_runner.py`:

```python
from dataclasses import replace
from models import IndexEntry


def indexed_entry():
    return IndexEntry(
        key="海外绘本/小老鼠迈尔斯/worldview",
        doc_token="doc-world", wiki_node_token="node-world",
        source_revisions={"node-a": "17"}, last_ai_revision_id=4,
        last_seen_revision_id=4, status="published",
    )


class IndexedPlane(FakeControlPlane):
    def __init__(self):
        super().__init__()
        self.index = {}
        self.updated = []

    def read_index(self):
        return self.index

    def update_index(self, entries):
        for item in entries:
            self.index[item.key] = item
            self.updated.append(item)
        return self.index


class RoutingPublisher(FakePublisher):
    def __init__(self):
        super().__init__()
        self.updated = []
        self.current = {"revision_id": 4, "content": "# 世界观\n"}
        self.history = {"revision_id": 4, "content": "# 世界观\n"}

    def fetch_current(self, doc_token):
        return self.current

    def fetch_revision(self, doc_token, revision_id):
        return self.history

    def conditional_update(self, entry, current, merged_markdown, source_revisions):
        self.updated.append((entry.key, merged_markdown, source_revisions))
        return replace(entry, source_revisions=dict(source_revisions),
                       last_ai_revision_id=current["revision_id"] + 2,
                       last_seen_revision_id=current["revision_id"] + 2)
```

Add tests:

```python
def test_absent_index_key_is_first_published(self):
    self._write_manifest()
    plane = IndexedPlane()
    publisher = RoutingPublisher()
    report = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane).publish(self.run_dir)
    self.assertEqual(publisher.published[0][0], "海外绘本/小老鼠迈尔斯/worldview")
    self.assertEqual(report["published"], 1)

def test_existing_key_with_same_source_vector_is_preserved(self):
    self._write_manifest()
    plane = IndexedPlane()
    plane.index["海外绘本/小老鼠迈尔斯/worldview"] = indexed_entry()
    publisher = RoutingPublisher()
    report = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane).publish(self.run_dir)
    self.assertEqual(publisher.published, [])
    self.assertEqual(publisher.updated, [])
    self.assertEqual(report["preserved"], 1)
    self.assertEqual(plane.updated[0].last_seen_revision_id, 4)

def test_existing_key_with_changed_source_vector_is_updated(self):
    self._write_manifest()
    plane = IndexedPlane()
    plane.index["海外绘本/小老鼠迈尔斯/worldview"] = replace(
        indexed_entry(), source_revisions={"node-a": "16"}
    )
    publisher = RoutingPublisher()
    report = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane).publish(self.run_dir)
    self.assertEqual(publisher.published, [])
    self.assertEqual(publisher.updated[0][0], "海外绘本/小老鼠迈尔斯/worldview")
    self.assertEqual(report["published"], 1)
```

- [ ] **Step 2: Run tests to verify routing is missing**

Run: `python plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_sync_runner.py -v`

Expected: the indexed-key tests fail because `publish_new()` is still used for every manifest entry.

- [ ] **Step 3: Implement remote-state routing**

Add imports:

```python
from dataclasses import replace
from merge_protocol import classify
from page_codec import parse_remote_page, render_remote_page
from publisher import NeedsReview
from target_state import classify_target
```

Inside `publish()`, after `remote_index = self.control_plane.read_index()`:

```python
preserved = 0
queued = 0
```

Replace the candidate loop body with:

```python
try:
    candidate, body = self._candidate(raw, run_dir)
    action = classify_target(candidate, remote_index)
    if action.action == "first_publish":
        result = self.publisher.publish_new(candidate, body, parent)
    elif action.action == "preserve":
        indexed = remote_index[candidate.key]
        self._assert_page_matches_index(indexed)
        result = replace(
            indexed,
            last_seen_revision_id=self.publisher.fetch_current(indexed.doc_token)["revision_id"],
        )
        preserved += 1
    elif action.action == "update":
        result = self._update_existing(remote_index[candidate.key], candidate, body)
    else:
        self.publisher.append_conflict(tokens["conflict"], {
            "key": candidate.key,
            "reason": action.reason,
            "revision_id": remote_index[candidate.key].last_seen_revision_id,
        })
        queued += 1
        continue
    self.control_plane.update_index([result])
    published += 1
    successful_pages += 1
    if successful_pages % 5 == 0:
        lease = self.control_plane.refresh_lock(lease, datetime.now(timezone.utc))
except Exception as error:
    failed += 1
    errors.append(f"{raw.get('key', '<unknown>')}: {error}")
```

Add helper methods to `SyncRunner`:

```python
def _assert_page_matches_index(self, indexed: IndexEntry) -> dict[str, Any]:
    current = self.publisher.fetch_current(indexed.doc_token)
    parsed = parse_remote_page(current["content"])
    metadata = parsed.metadata
    if (
        metadata["key"] != indexed.key
        or metadata["source_revisions"] != indexed.source_revisions
        or metadata["last_ai_revision_id"] != indexed.last_ai_revision_id
    ):
        raise NeedsReview("remote page metadata does not match the remote index")
    return current

def _update_existing(self, indexed: IndexEntry, candidate: IndexEntry, body: str) -> IndexEntry:
    current = self._assert_page_matches_index(indexed)
    current_page = parse_remote_page(current["content"])
    history = self.publisher.fetch_revision(indexed.doc_token, indexed.last_ai_revision_id)
    base_page = parse_remote_page(history["content"])
    requirement = classify(base_page.body, current_page.body, body, source_changed=True)
    if requirement.required_action == "preserve":
        return replace(indexed, last_seen_revision_id=current["revision_id"])
    if requirement.required_action != "publish":
        raise NeedsReview(requirement.reason)
    merged = render_remote_page(body, candidate.metadata)
    return self.publisher.conditional_update(
        indexed, current, merged, candidate.source_revisions,
    )
```

Remove the old `_entry()` method. In the final `SyncReport`, replace the fixed `preserved=0, queued=0` values with the actual `preserved` and `queued` counters.

- [ ] **Step 4: Run sync-runner tests**

Run: `python plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_sync_runner.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_runner.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_runner.py
git commit -m "feat: route sync candidates by remote state"
```

### Task 6: Queue Orphan Pages and Index Failures

**Files:**

- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_runner.py`
- Modify: `plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_runner.py`

**Interfaces:**

- Consumes: `Publisher.append_conflict()`, `Publisher.publish_new()`'s existing-page rejection, and `NeedsReview`.
- Produces: review-case reporting through the remote conflict queue; page-success-plus-index-failure is queued, not reported as published.

- [ ] **Step 1: Write failing tests**

Extend `RoutingPublisher` with conflict recording:

```python
    def __init__(self):
        super().__init__()
        self.conflicts = []

    def append_conflict(self, parent, record):
        self.conflicts.append((parent, record))
```

Add a page-metadata helper near the top of the test additions:

```python
from page_codec import render_remote_page


def indexed_remote_page(source_revision="17"):
    return render_remote_page("# 世界观\n", {
        "schema_version": 1,
        "key": "海外绘本/小老鼠迈尔斯/worldview",
        "page_type": "worldview",
        "source_node_tokens": ["node-a"],
        "source_revisions": {"node-a": source_revision},
        "last_ai_revision_id": 4,
    })
```

Add tests:

```python
def test_target_page_without_index_entry_is_queued(self):
    self._write_manifest()
    plane = IndexedPlane()
    publisher = RoutingPublisher()
    publisher.publish_new = lambda *args, **kwargs: (_ for _ in ()).throw(
        NeedsReview("logical key page already exists")
    )
    report = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane).publish(self.run_dir)
    self.assertEqual(report["queued"], 1)
    self.assertEqual(report["published"], 0)
    self.assertEqual(publisher.conflicts[0][1]["key"], "海外绘本/小老鼠迈尔斯/worldview")

def test_page_success_with_index_failure_is_queued(self):
    self._write_manifest()
    plane = IndexedPlane()
    plane.update_index = lambda entries: (_ for _ in ()).throw(
        ControlPlaneCorrupt("index readback did not match")
    )
    publisher = RoutingPublisher()
    report = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane).publish(self.run_dir)
    self.assertEqual(report["queued"], 1)
    self.assertEqual(report["published"], 0)
    self.assertEqual(publisher.conflicts[0][1]["reason"], "index_update_failed: index readback did not match")

def test_missing_indexed_page_is_queued(self):
    self._write_manifest()
    plane = IndexedPlane()
    plane.index["海外绘本/小老鼠迈尔斯/worldview"] = indexed_entry()
    publisher = RoutingPublisher()
    publisher.fetch_current = lambda doc_token: (_ for _ in ()).throw(
        NeedsReview("missing current page")
    )
    report = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane).publish(self.run_dir)
    self.assertEqual(report["queued"], 1)
    self.assertEqual(report["failed"], 0)

def test_page_metadata_drift_from_index_is_queued(self):
    self._write_manifest()
    plane = IndexedPlane()
    plane.index["海外绘本/小老鼠迈尔斯/worldview"] = indexed_entry()
    publisher = RoutingPublisher()
    publisher.current = {"revision_id": 4, "content": indexed_remote_page("16")}
    report = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane).publish(self.run_dir)
    self.assertEqual(report["queued"], 1)
    self.assertEqual(report["failed"], 0)
    self.assertEqual(publisher.conflicts[0][1]["reason"], "remote page metadata does not match the remote index")
```

- [ ] **Step 2: Run tests to verify review routing is missing**

Run: `python plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_sync_runner.py -v`

Expected: the new tests fail because these cases are still counted as generic failures.

- [ ] **Step 3: Implement explicit review handling**

Refactor the candidate loop's success/index-update section:

```python
try:
    self.control_plane.update_index([result])
except Exception as index_error:
    self.publisher.append_conflict(tokens["conflict"], {
        "key": candidate.key,
        "reason": f"index_update_failed: {index_error}",
        "revision_id": result.last_seen_revision_id,
    })
    queued += 1
    continue
published += 1
successful_pages += 1
if successful_pages % 5 == 0:
    lease = self.control_plane.refresh_lock(lease, datetime.now(timezone.utc))
```

The final implementation calls `update_index()` exactly once for each page result. If that call raises, the next operation is the conflict append, and the candidate is counted as queued.

Change the outer exception handler to distinguish review cases:

```python
except NeedsReview as error:
    key = raw.get("key", "<unknown>")
    revision = remote_index[key].last_seen_revision_id if key in remote_index else 0
    self.publisher.append_conflict(tokens["conflict"], {
        "key": key,
        "reason": str(error),
        "revision_id": revision,
    })
    queued += 1
except Exception as error:
    failed += 1
    errors.append(f"{raw.get('key', '<unknown>')}: {error}")
```

Use the exact production form shown above; do not import test helpers into `sync_runner.py`.

- [ ] **Step 4: Run sync-runner tests**

Run: `python plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\test_sync_runner.py -v`

Expected: all tests pass.

- [ ] **Step 5: Run a placeholder scan on the touched file**

Run: `rg -n "TODO|TBD|implement later|keep this line only" plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\sync_runner.py`

Expected: no matches.

- [ ] **Step 6: Commit**

```bash
git add plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/sync_runner.py plugins/picturebook-screenwriter/skills/feishu-knowledge-store/scripts/test_sync_runner.py
git commit -m "fix: queue orphan and index-failure review cases"
```

### Task 7: Update the Compatibility Contract

**Files:**

- Modify: `docs/workbuddy-feishu-knowledge-base-compatibility.md`

**Interfaces:**

- Consumes: approved spec sections 3, 4, 5, and 6.
- Produces: explicit protocol wording that task-local files are not cross-run target state.

- [ ] **Step 1: Extend authority and forbidden-state rules**

In section 1, after item 2, add:

```markdown
2a. The shared sync-state file is exactly `99_系统控制台/AI_KB_INDEX_V1`. Target publication
    status and revision baselines must be read from and written back to that remote document.
    Current-run source snapshots, delta state, manifests, and run reports may inform this run
    but must not reconstruct, override, or replace the remote index.
```

In section 11, after item 11, add:

```markdown
11a. 禁止用当前运行的 `nodes_snapshot.json`、`delta_state.json`、`_manifest.json`、
     `sync_report.json` 或知识检索缓存恢复、替代或重建远端同步索引。
```

- [ ] **Step 2: Verify the wording is present**

Run: `rg -n "AI_KB_INDEX_V1|nodes_snapshot.json|delta_state.json|sync_report.json" docs\workbuddy-feishu-knowledge-base-compatibility.md`

Expected: the new authority and prohibition lines appear.

- [ ] **Step 3: Commit**

```bash
git add docs/workbuddy-feishu-knowledge-base-compatibility.md
git commit -m "docs: define remote sync state authority boundary"
```

### Task 8: Run Full Regression

**Files:**

- No new files.

**Interfaces:**

- Consumes: all tasks above.
- Produces: verification that store, ingest, and plugin contract suites remain green.

- [ ] **Step 1: Run all Feishu store tests**

Run: `python -m unittest discover -s plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts -p "test_*.py" -v`

Expected: all tests pass.

- [ ] **Step 2: Run all wiki-ingest tests**

Run: `python -m unittest discover -s plugins\picturebook-screenwriter\skills\wiki-ingest\scripts -p "test_*.py" -v`

Expected: all tests pass.

- [ ] **Step 3: Run plugin contract tests**

Run: `python -m unittest discover -s plugins\picturebook-screenwriter\tests -p "test_*.py" -v`

Expected: all tests pass.

- [ ] **Step 4: Check forbidden local-state authority language**

Run: `rg -n "状态必须写入同步索引|私有状态文件|AI_KB_INDEX_V1|同步状态文件" docs\workbuddy-feishu-knowledge-base-compatibility.md docs\superpowers\specs\2026-09-22-feishu-kb-ai-sync-state-authority-design.md`

Expected: remote-index authority language is present in both documents.

- [ ] **Step 5: Commit any formatting-only cleanup**

If files were reformatted, run:

```bash
git add plugins/picturebook-screenwriter docs/workbuddy-feishu-knowledge-base-compatibility.md
git commit -m "test: verify remote sync state authority"
```

If there is no output from `git status --short`, skip this step.
