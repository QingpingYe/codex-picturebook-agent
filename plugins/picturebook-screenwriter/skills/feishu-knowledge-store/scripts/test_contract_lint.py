import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from contract_lint import run_lint
from page_codec import render_remote_page


def entry(key="s/p/worldview", doc_token="page-doc", node_token="node-page"):
    return {
        "key": key, "doc_token": doc_token, "wiki_node_token": node_token,
        "source_revisions": {"node": "1"}, "last_ai_revision_id": 4,
        "last_seen_revision_id": 4, "status": "published",
    }


def make_valid_fixture(root, duplicate_key=False, bad_conflict=False):
    fixture = Path(root) / "fixture"
    pages = fixture / "pages"
    pages.mkdir(parents=True)

    entries = [entry()]
    if duplicate_key:
        entries.append(entry(doc_token="other-doc", node_token="other-node"))

    index = "# AI_KB_INDEX_V1\n```json\n" + json.dumps({
        "schema_version": 1, "entries": entries,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n```\n"
    (fixture / "index.md").write_text(index, encoding="utf-8")

    conflict = "# AI_KB_CONFLICT_QUEUE_V1\n"
    if bad_conflict:
        conflict += "[broken conflict record\n"
    (fixture / "conflict.md").write_text(conflict, encoding="utf-8")

    page = render_remote_page("# 世界观\n", {
        "schema_version": 1, "key": "s/p/worldview", "page_type": "worldview",
        "source_node_tokens": ["node"], "source_revisions": {"node": "1"},
        "last_ai_revision_id": 4,
    })
    (pages / "s__p__worldview.md").write_text(page, encoding="utf-8")

    tree = [
        {"node_token": "root", "title": "<root>", "parent_node_token": None},
        {"node_token": "node-page", "title": "s/p/worldview", "parent_node_token": "root"},
    ]
    if duplicate_key:
        tree.append({
            "node_token": "other-node", "title": "s/p/worldview",
            "parent_node_token": "root",
        })
    (fixture / "tree.json").write_text(json.dumps(tree), encoding="utf-8")
    return fixture


def run_lint_at(root, **options):
    return run_lint(make_valid_fixture(root, **options))


class ContractLintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_valid_fixture_passes(self):
        result = run_lint_at(self.tmp.name)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["exit_code"], 0)

    def test_duplicate_logical_key_fails(self):
        result = run_lint_at(self.tmp.name, duplicate_key=True)
        self.assertIn("duplicate logical key", result["errors"][0])
        self.assertEqual(result["exit_code"], 1)

    def test_malformed_conflict_record_fails(self):
        result = run_lint_at(self.tmp.name, bad_conflict=True)
        self.assertIn("malformed conflict record", result["errors"][0])
        self.assertEqual(result["exit_code"], 1)


if __name__ == "__main__":
    unittest.main()
