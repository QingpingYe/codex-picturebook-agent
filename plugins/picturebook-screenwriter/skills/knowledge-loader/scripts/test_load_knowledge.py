import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from load_knowledge import KnowledgeEvidenceBundle, KnowledgeLoader, KnowledgeQuery
from page_codec import render_remote_page


def remote_page(key: str, page_type: str, body: str) -> str:
    return render_remote_page(body, {
        "key": key,
        "page_type": page_type,
        "source_node_tokens": ["source"],
        "source_revisions": {"source": "r1"},
        "last_ai_revision_id": 1,
    })


class FakePlane:
    def read_index(self):
        from models import IndexEntry
        return {
            "海外绘本/小老鼠迈尔斯/worldview": IndexEntry(
                key="海外绘本/小老鼠迈尔斯/worldview", doc_token="doc-world",
                wiki_node_token="node-world", source_revisions={"source": "r1"},
                last_ai_revision_id=44, last_seen_revision_id=44, status="published",
            ),
            "海外绘本/小老鼠迈尔斯/characters": IndexEntry(
                key="海外绘本/小老鼠迈尔斯/characters", doc_token="doc-char",
                wiki_node_token="node-char", source_revisions={"source": "r1"},
                last_ai_revision_id=45, last_seen_revision_id=45, status="needs_review",
            ),
        }


class FakeCli:
    def fetch_doc(self, token):
        content_by_token = {
            "doc-world": remote_page(
                "海外绘本/小老鼠迈尔斯/worldview", "worldview", "# 世界观\n\n小老鼠迈尔斯住在森林里。"),
            "doc-char": remote_page(
                "海外绘本/小老鼠迈尔斯/characters", "characters", "# 角色\n\n铃铛是他的重要道具。"),
        }
        return {"data": {"document": {"revision_id": 45, "content": content_by_token[token]}}}


class KnowledgeLoaderTests(unittest.TestCase):
    def test_loader_reads_remote_page_and_strips_metadata(self):
        loader = KnowledgeLoader(FakePlane(), FakeCli())
        bundle = loader.load(KnowledgeQuery(project_id="小老鼠迈尔斯", terms=("角色", "铃铛")))
        self.assertEqual(bundle.items[0].key, "海外绘本/小老鼠迈尔斯/characters")
        self.assertEqual(bundle.items[0].revision_id, 45)
        self.assertNotIn("系统元数据", bundle.items[0].content)

    def test_loader_includes_needs_review_warning(self):
        loader = KnowledgeLoader(FakePlane(), FakeCli())
        bundle = loader.load(KnowledgeQuery(project_id="小老鼠迈尔斯", terms=("角色", "铃铛")))
        self.assertTrue(any("存在待处理冲突" in warning for warning in bundle.warnings))

    def test_loader_can_fall_back_to_cached_bundle_with_offline_warning(self):
        cached = KnowledgeEvidenceBundle(
            items=[], warnings=["缓存"], offline=False,
            fetched_at="2026-09-17T10:00:00+08:00",
        )
        class FailingPlane:
            def read_index(self):
                raise RuntimeError("network down")

        loader = KnowledgeLoader(FailingPlane(), FakeCli(), cached_bundle=cached)
        bundle = loader.load(
            KnowledgeQuery(project_id="小老鼠迈尔斯", terms=("角色", "铃铛")),
            allow_offline_cache=True,
        )
        self.assertTrue(bundle.offline)
        self.assertIn("最后确认的本地缓存", bundle.warnings[0])
        self.assertEqual(bundle.fetched_at, "2026-09-17T10:00:00+08:00")


if __name__ == "__main__":
    unittest.main()
