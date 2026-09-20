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


def entry():
    return IndexEntry(
        key="s/p/worldview", doc_token="doc-worldview", wiki_node_token="node-worldview",
        source_revisions={"source": "r1"}, last_ai_revision_id=1,
        last_seen_revision_id=1, status="published",
    )


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

    def fetch_current(self, doc_token):
        current = self.pages.get(doc_token)
        if not current:
            raise IndexError(f"missing current page: {doc_token}")
        return {"revision_id": current.revision_id, "content": current.content}

    def append_conflict(self, parent, record):
        self.conflicts.append((parent, record))


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

    def update_index(self, entries):
        return {entry.key: entry for entry in entries}


class EndToEndTests(unittest.TestCase):
    def test_two_sync_users_and_human_edit_never_lose_human_content(self):
        plane = FakeControlPlane()
        publisher = FakePublisher()
        service = SyncService(publisher, plane, conflict_parent="conflict-doc")

        decision = MergeDecision(
            key="s/p/worldview",
            action="queue",
            merged_markdown=None,
            reason="人工内容与新源资料冲突",
        )
        report = service.apply([decision], {decision.key: entry()})
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
