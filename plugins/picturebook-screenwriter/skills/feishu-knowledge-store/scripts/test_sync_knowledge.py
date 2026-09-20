import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

SCRIPTS = Path(__file__).parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lark_cli import RevisionConflict
from merge_protocol import MergeDecision
from models import IndexEntry
from sync_knowledge import SyncReport, SyncService


def page(body="# 正文"):
    return body


def entry():
    return IndexEntry(
        key="s/p/worldview", doc_token="doc-worldview", wiki_node_token="node-worldview",
        source_revisions={"source": "r1"}, last_ai_revision_id=1,
        last_seen_revision_id=1, status="published",
    )


@dataclass
class FakePage:
    revision_id: int
    content: str


class FakePublisher:
    def __init__(self):
        self.pages = {"doc-worldview": FakePage(1, "# 人工设定")}
        self.updates = []
        self.conflicts = []
        self.conflicts_before_success = 0

    def conditional_update(self, entry, current, content, source_revisions):
        self.updates.append((entry.key, content))
        if self.conflicts_before_success:
            self.conflicts_before_success -= 1
            raise RevisionConflict("changed")
        self.pages[entry.doc_token].revision_id += 1
        self.pages[entry.doc_token].content = content
        return entry

    def fetch_current(self, doc_token):
        current = self.pages.get(doc_token)
        if not current:
            raise IndexError(f"missing current page: {doc_token}")
        return {"revision_id": current.revision_id, "content": current.content}

    def append_conflict(self, parent, record):
        self.conflicts.append((parent, record))


class FakePlane:
    def acquire_lock(self, holder, now):
        return object()

    def release_lock(self, lease):
        self.released = True

    def update_index(self, entries):
        return {entry.key: entry for entry in entries}


class SyncServiceTests(unittest.TestCase):
    def setUp(self):
        self.publisher = FakePublisher()
        self.service = SyncService(self.publisher, FakePlane(), conflict_parent="conflict-doc")

    def test_apply_uses_matching_entry_for_each_decision(self):
        decision = MergeDecision(
            key="s/p/worldview", action="publish",
            merged_markdown=page("# 人工设定\n\n新资料"), reason="source only",
        )
        self.service.apply([decision], {decision.key: entry()})
        self.assertEqual(self.publisher.updates[0][0], "s/p/worldview")

    def test_queue_sends_conflict_parent(self):
        decision = MergeDecision(key="s/p/worldview", action="queue",
                                 merged_markdown=None, reason="human conflict")
        self.service.apply([decision], {decision.key: entry()})
        self.assertEqual(self.publisher.conflicts[0], ("conflict-doc", {
            "key": "s/p/worldview", "reason": "human conflict",
            "revision_id": 1,
        }))

    def test_queue_preserves_human_page(self):
        decision = MergeDecision(key="s/p/worldview", action="queue", merged_markdown=None,
                                 reason="人工内容与新资料冲突")
        report = self.service.apply([decision], {decision.key: entry()})
        self.assertEqual(report, SyncReport(published=0, preserved=0, queued=1,
                                            failed=0, retried=0))
        self.assertEqual(self.publisher.pages["doc-worldview"].content, "# 人工设定")

    def test_second_conflict_queues_after_one_retry(self):
        self.publisher.conflicts_before_success = 2
        decision = MergeDecision(key="s/p/worldview", action="publish",
                                 merged_markdown="# 人工设定\n\n新资料",
                                 reason="保留人工内容")
        report = self.service.apply([decision], {decision.key: entry()})
        self.assertEqual(report, SyncReport(published=0, preserved=0, queued=1,
                                            failed=0, retried=1))


if __name__ == "__main__":
    unittest.main()
