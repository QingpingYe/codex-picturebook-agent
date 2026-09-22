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
