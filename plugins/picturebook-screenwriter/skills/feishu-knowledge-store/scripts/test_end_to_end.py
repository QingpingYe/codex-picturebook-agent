import sys
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

KNOWLEDGE_SCRIPTS = SCRIPTS.parent.parent / "knowledge-loader" / "scripts"
if str(KNOWLEDGE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(KNOWLEDGE_SCRIPTS))

from control_plane import LockHeld
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
        self.conflicts = []

    def conditional_update(self, entry, current, content, source_revisions):
        page = self.pages[entry.doc_token]
        if current["content"] != page.content:
            raise RevisionConflict("changed elsewhere")
        page.revision_id += 1
        page.content = content
        return entry

    def append_conflict(self, record):
        self.conflicts.append(record)


class FakeControlPlane:
    def __init__(self):
        self.lock_holders = []

    def acquire_lock(self, holder, now):
        if self.lock_holders:
            raise LockHeld("lock held")
        self.lock_holders.append(holder)
        return holder

    def release_lock(self, lease):
        self.lock_holders.remove(lease)


class EndToEndTests(unittest.TestCase):
    def test_two_sync_users_and_human_edit_never_lose_human_content(self):
        plane = FakeControlPlane()
        publisher = FakePublisher()
        service = SyncService(publisher, plane)

        decision = MergeDecision(
            key="s/p/worldview",
            action="queue",
            merged_markdown=None,
            reason="人工内容与新源资料冲突",
        )
        report = service.apply([decision])
        self.assertEqual(report, SyncReport(published=0, preserved=0, queued=1,
                                             failed=0, retried=0))
        self.assertTrue(publisher.conflicts)
        self.assertEqual(publisher.pages["doc-worldview"].content, "# 人工设定")

        held_lease = plane.acquire_lock("alice@host", datetime.now(timezone.utc))
        with self.assertRaises(LockHeld):
            plane.acquire_lock("bob@host", datetime.now(timezone.utc))
        plane.release_lock(held_lease)


if __name__ == "__main__":
    unittest.main()
