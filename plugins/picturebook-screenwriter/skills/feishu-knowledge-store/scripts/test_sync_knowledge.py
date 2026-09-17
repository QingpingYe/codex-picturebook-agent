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


@dataclass
class FakePage:
    revision_id: int
    content: str


class FakePublisher:
    def __init__(self):
        self.pages = {"doc-worldview": FakePage(1, "# 人工设定")}
        self.updates = []
        self.conflicts_before_success = 0

    def conditional_update(self, entry, current, content, source_revisions):
        self.updates.append((entry.key, content))
        if self.conflicts_before_success:
            self.conflicts_before_success -= 1
            raise RevisionConflict("changed")
        self.pages[entry.doc_token].revision_id += 1
        self.pages[entry.doc_token].content = content
        return entry

    def append_conflict(self, record):
        self.record = record


class FakePlane:
    def acquire_lock(self, holder, now):
        return object()

    def release_lock(self, lease):
        self.released = True


class SyncServiceTests(unittest.TestCase):
    def setUp(self):
        self.publisher = FakePublisher()
        self.service = SyncService(self.publisher, FakePlane())

    def test_queue_preserves_human_page(self):
        decision = MergeDecision(key="s/p/worldview", action="queue", merged_markdown=None,
                                 reason="人工内容与新资料冲突")
        report = self.service.apply([decision])
        self.assertEqual(report, SyncReport(published=0, preserved=0, queued=1,
                                            failed=0, retried=0))
        self.assertEqual(self.publisher.pages["doc-worldview"].content, "# 人工设定")

    def test_second_conflict_queues_after_one_retry(self):
        self.publisher.conflicts_before_success = 2
        decision = MergeDecision(key="s/p/worldview", action="publish",
                                 merged_markdown="# 人工设定\n\n新资料",
                                 reason="保留人工内容")
        report = self.service.apply([decision])
        self.assertEqual(report, SyncReport(published=0, preserved=0, queued=1,
                                            failed=0, retried=1))


if __name__ == "__main__":
    unittest.main()
