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


def make_valid_fixture(root, duplicate_key=False, malformed_index=False,
                       malformed_page=False, tree_not_array=False,
                       index_last_seen=None):
    fixture = Path(root) / "fixture"
    pages = fixture / "pages"
    pages.mkdir(parents=True)

    entries = [entry()]
    if duplicate_key:
        entries.append(entry(doc_token="other-doc", node_token="other-node"))
    if index_last_seen is not None:
        entries[0]["last_seen_revision_id"] = index_last_seen

    if malformed_index:
        payload = {"entries": entries}
    else:
        payload = {"schema_version": 1, "entries": entries}
    index = "# AI_KB_INDEX_V1\n```json\n" + json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ) + "\n```\n"
    (fixture / "index.md").write_text(index, encoding="utf-8")

    page = render_remote_page("# 世界观\n", {
        "schema_version": 1, "key": "s/p/worldview", "page_type": "worldview",
        "source_node_tokens": ["node"], "source_revisions": {"node": "1"},
        "last_ai_revision_id": 4,
    })
    if malformed_page:
        page = page.replace('"page_type":"worldview",', "")
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
    tree_payload = {"nodes": tree} if tree_not_array else tree
    (fixture / "tree.json").write_text(json.dumps(tree_payload), encoding="utf-8")
    return fixture


def run_lint_at(root, **options):
    return run_lint(make_valid_fixture(root, **options))


class ContractLintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_valid_fixture_without_conflict_page_passes(self):
        fixture = make_valid_fixture(self.tmp.name)
        self.assertFalse((fixture / "conflict.md").exists())
        result = run_lint(fixture)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["exit_code"], 0)

    def test_valid_fixture_accepts_feishu_blank_line(self):
        fixture = make_valid_fixture(self.tmp.name)
        path = fixture / "index.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "# AI_KB_INDEX_V1\n```json\n",
                "# AI_KB_INDEX_V1\n\n```json\n",
            ),
            encoding="utf-8",
        )
        result = run_lint(fixture)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["exit_code"], 0)

    def test_display_title_does_not_break_index_tree_mapping(self):
        fixture = make_valid_fixture(self.tmp.name)
        tree_path = fixture / "tree.json"
        tree = json.loads(tree_path.read_text(encoding="utf-8"))
        for node in tree:
            if node.get("node_token") == "node-page":
                node["title"] = "世界观显示标题"
        tree_path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
        result = run_lint(fixture)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["exit_code"], 0)

    def test_duplicate_logical_key_fails(self):
        result = run_lint_at(self.tmp.name, duplicate_key=True)
        self.assertIn("duplicate logical key", result["errors"][0])
        self.assertEqual(result["exit_code"], 1)

    def test_malformed_index_schema_fails(self):
        result = run_lint_at(self.tmp.name, malformed_index=True)
        self.assertIn("invalid index fixture", result["errors"][0])
        self.assertEqual(result["exit_code"], 1)

    def test_malformed_page_metadata_fails(self):
        result = run_lint_at(self.tmp.name, malformed_page=True)
        self.assertIn("invalid page fixture", result["errors"][0])
        self.assertEqual(result["exit_code"], 1)

    def test_tree_must_be_an_array(self):
        result = run_lint_at(self.tmp.name, tree_not_array=True)
        self.assertIn("tree fixture must be a JSON array", result["errors"][0])
        self.assertEqual(result["exit_code"], 1)

    def test_observed_revision_may_advance_past_ai_revision(self):
        result = run_lint_at(self.tmp.name, index_last_seen=9)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["exit_code"], 0)

    def test_observed_revision_may_not_precede_ai_revision(self):
        result = run_lint_at(self.tmp.name, index_last_seen=3)
        self.assertIn("last_seen_revision_id", result["errors"][0])
        self.assertEqual(result["exit_code"], 1)


if __name__ == "__main__":
    unittest.main()
