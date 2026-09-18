import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "skills" / "knowledge-loader" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from authority import AuthorityLoader, AuthorityQuery
from collision import check_collisions
from dependencies import (
    build_dependency_record,
    find_stale_dependencies,
    parse_dependency_record,
    render_dependency_record,
)
from models import IndexEntry
from page_codec import render_remote_page


BUNDLE = {
    "items": [{
        "key": "海外绘本/小老鼠迈尔斯/worldview",
        "doc_token": "doc-a",
        "revision_id": 42,
        "content": "迈尔斯住在一座蓝色树屋里。",
        "source_revisions": {"node-a": "17"},
    }],
    "warnings": (),
    "offline": False,
    "fetched_at": "2026-09-18T10:00:00+08:00",
}


class KnowledgeLoopTests(unittest.TestCase):
    def test_lock_collision_and_stale(self):
        record = build_dependency_record(BUNDLE, "demo-script-v1", "script")
        hits = check_collisions("迈尔斯住在红色火车里。", BUNDLE, ("迈尔斯", "蓝色树屋"))
        self.assertTrue(hits)
        stale = find_stale_dependencies(record, {
            "海外绘本/小老鼠迈尔斯/worldview": {
                "key": "海外绘本/小老鼠迈尔斯/worldview",
                "revision_id": 43,
                "status": "published",
            }
        })
        self.assertEqual(stale[0].reason, "revision_changed")

    def test_create_store_reload_and_check_saved_artifact(self):
        index = {
            "海外绘本/小老鼠迈尔斯/worldview": IndexEntry(
                key="海外绘本/小老鼠迈尔斯/worldview",
                doc_token="doc-a",
                wiki_node_token="node-a",
                source_revisions={"node-a": "17"},
                last_ai_revision_id=42,
                last_seen_revision_id=42,
                status="published",
            )
        }
        page = render_remote_page("# 世界观\n\n迈尔斯住在一座蓝色树屋里。", {
            "key": "海外绘本/小老鼠迈尔斯/worldview",
            "page_type": "worldview",
            "source_node_tokens": ["node-a"],
            "source_revisions": {"node-a": "17"},
            "last_ai_revision_id": 42,
        })
        cli = _MutablePageCli({"doc-a": (42, page)})
        loader = AuthorityLoader(_StaticIndexPlane(index), cli)
        bundle = loader.load(AuthorityQuery("小老鼠迈尔斯", "海外绘本", ("worldview",)))
        record = build_dependency_record(bundle, "worldview-v1", "worldview")

        body = "# 世界观\n\n迈尔斯住在一座蓝色树屋里。"
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "worldview_v1.md"
            artifact.write_text(body + "\n\n" + render_dependency_record(record), encoding="utf-8")
            reloaded = parse_dependency_record(artifact.read_text(encoding="utf-8"))
            self.assertTrue(artifact.read_text(encoding="utf-8").startswith(body))
            self.assertEqual(reloaded, record)

        hits = check_collisions("迈尔斯住在红色火车里。", bundle, ("迈尔斯", "蓝色树屋"))
        self.assertTrue(hits)
        current_bundle = loader.load(AuthorityQuery("小老鼠迈尔斯", "海外绘本", ("worldview",)))
        self.assertEqual(find_stale_dependencies(record, index, current_bundle), ())

        cli.pages["doc-a"] = (43, page)
        fetched_bundle = loader.load(AuthorityQuery("小老鼠迈尔斯", "海外绘本", ("worldview",)))
        stale = find_stale_dependencies(record, index, fetched_bundle)
        self.assertEqual(stale[0].reason, "revision_changed")
        self.assertEqual(stale[0].current_revision_id, 43)


class _StaticIndexPlane:
    def __init__(self, index):
        self.index = index

    def read_index(self):
        return self.index


class _MutablePageCli:
    def __init__(self, pages):
        self.pages = pages

    def fetch_doc(self, token):
        revision, content = self.pages[token]
        return {"data": {"document": {"revision_id": revision, "content": content}}}


if __name__ == "__main__":
    unittest.main()
