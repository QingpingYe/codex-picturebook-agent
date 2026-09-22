import sys
import unittest
from dataclasses import replace
from pathlib import Path

SCRIPTS = Path(__file__).parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from load_knowledge import (
    KnowledgeEvidence,
    KnowledgeEvidenceBundle,
    KnowledgeLoader,
    KnowledgeQuery,
    bundle_from_dict,
    bundle_to_dict,
)
from page_codec import render_remote_page


def remote_page(key: str, page_type: str, body: str, last_ai_revision_id: int = 1) -> str:
    return render_remote_page(body, {
        "key": key,
        "page_type": page_type,
        "source_node_tokens": ["source"],
        "source_revisions": {"source": "r1"},
        "last_ai_revision_id": last_ai_revision_id,
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
                "海外绘本/小老鼠迈尔斯/worldview", "worldview",
                "# 世界观\n\n小老鼠迈尔斯住在森林里。", last_ai_revision_id=44),
            "doc-char": remote_page(
                "海外绘本/小老鼠迈尔斯/characters", "characters",
                "# 角色\n\n铃铛是他的重要道具。", last_ai_revision_id=45),
        }
        revision_by_token = {"doc-world": 44, "doc-char": 45}
        return {"data": {"document": {
            "revision_id": revision_by_token[token], "content": content_by_token[token],
        }}}


class KnowledgeLoaderTests(unittest.TestCase):
    def test_loader_reads_remote_page_and_strips_metadata(self):
        loader = KnowledgeLoader(FakePlane(), FakeCli())
        bundle = loader.load(KnowledgeQuery(project_id="小老鼠迈尔斯", terms=("角色", "铃铛")))
        self.assertEqual(bundle.items[0].key, "海外绘本/小老鼠迈尔斯/characters")
        self.assertEqual(bundle.items[0].revision_id, 45)
        self.assertTrue(bundle.items[0].index_synced)
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

    def test_loader_rejects_revision_skew_between_page_and_index(self):
        plane = FakePlane()
        skewed = replace(
            plane.read_index()["海外绘本/小老鼠迈尔斯/worldview"],
            last_ai_revision_id=44, last_seen_revision_id=45,
        )
        plane.read_index = lambda: {skewed.key: skewed}
        loader = KnowledgeLoader(plane, FakeCli())
        with self.assertRaisesRegex(ValueError, "revision"):
            loader.load(KnowledgeQuery(project_id="小老鼠迈尔斯"))

    def test_loader_marks_page_ahead_of_index_as_not_synced(self):
        plane = FakePlane()
        behind_index = replace(
            plane.read_index()["海外绘本/小老鼠迈尔斯/worldview"],
            last_ai_revision_id=44, last_seen_revision_id=43,
        )
        plane.read_index = lambda: {behind_index.key: behind_index}
        loader = KnowledgeLoader(plane, FakeCli())
        bundle = loader.load(KnowledgeQuery(project_id="小老鼠迈尔斯"))

        self.assertEqual(len(bundle.items), 1)
        self.assertFalse(bundle.items[0].index_synced)
        self.assertEqual(bundle.items[0].status, "published")
        self.assertTrue(any("索引尚未同步" in warning for warning in bundle.warnings))

    def test_bundle_serialization_preserves_index_sync_state(self):
        item = KnowledgeEvidence(
            key="海外绘本/小老鼠迈尔斯/worldview",
            doc_token="doc-world",
            revision_id=45,
            title="海外绘本/小老鼠迈尔斯/worldview",
            content="# 世界观",
            source_revisions={"source": "r1"},
            status="published",
            index_synced=False,
        )
        bundle = KnowledgeEvidenceBundle(
            items=(item,),
            warnings=("索引尚未同步",),
            offline=False,
            fetched_at="2026-09-22T10:00:00+08:00",
        )

        restored = bundle_from_dict(bundle_to_dict(bundle))

        self.assertIsNotNone(restored)
        self.assertFalse(restored.items[0].index_synced)

    def test_loader_uses_exact_project_segment(self):
        loader = KnowledgeLoader(FakePlane(), FakeCli())
        bundle = loader.load(KnowledgeQuery(project_id="小老鼠"))
        self.assertEqual(bundle.items, ())

    def test_loader_skips_archived_entries_with_warning(self):
        plane = FakePlane()
        old = plane.read_index()["海外绘本/小老鼠迈尔斯/worldview"]
        plane.read_index = lambda: {old.key: replace(old, status="archived")}
        loader = KnowledgeLoader(plane, FakeCli())
        bundle = loader.load(KnowledgeQuery(project_id="小老鼠迈尔斯"))
        self.assertEqual(bundle.items, ())
        self.assertTrue(any("已归档" in warning for warning in bundle.warnings))


if __name__ == "__main__":
    unittest.main()
