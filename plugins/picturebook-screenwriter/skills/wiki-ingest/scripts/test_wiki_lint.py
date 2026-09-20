import sys
import unittest
from pathlib import Path

KNOWLEDGE_STORE = Path(__file__).parents[2] / "feishu-knowledge-store" / "scripts"
if str(KNOWLEDGE_STORE) not in sys.path:
    sys.path.insert(0, str(KNOWLEDGE_STORE))

from models import IndexEntry
from wiki_lint import lint_index


class WikiLintTests(unittest.TestCase):
    def test_published_index_is_clean(self):
        index = {
            "a/b/worldview": {"key": "a/b/worldview", "status": "published"},
        }
        self.assertEqual(lint_index(index), ())

    def test_needs_review_is_warned(self):
        index = {"a/b/worldview": {"key": "a/b/worldview", "status": "needs_review"}}
        findings = lint_index(index)
        self.assertEqual(findings[0].severity, "WARN")

    def test_control_plane_index_entry_contract_is_supported(self):
        index = {
            "a/b/worldview": IndexEntry(
                key="a/b/worldview",
                doc_token="doc",
                wiki_node_token="node",
                source_revisions={},
                last_ai_revision_id=1,
                last_seen_revision_id=1,
                status="published",
            ),
        }
        self.assertEqual(lint_index(index), ())


if __name__ == "__main__":
    unittest.main()
