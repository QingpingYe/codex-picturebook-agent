import unittest
from types import SimpleNamespace

from authority import AuthorityLoader, AuthorityQuery, AuthorityGapError
from models import IndexEntry
from page_codec import render_remote_page
from load_knowledge import KnowledgeEvidence, KnowledgeEvidenceBundle


class FakeControlPlane:
    def read_index(self):
        return {
            "海外绘本/小老鼠迈尔斯/worldview": SimpleNamespace(
                key="海外绘本/小老鼠迈尔斯/worldview",
                doc_token="doc-a",
                wiki_node_token="wiki-a",
                source_revisions={"node-a": "17"},
                last_ai_revision_id=42,
                last_seen_revision_id=42,
                status="published",
            )
        }


class FakeCli:
    def fetch_doc(self, token):
        return {"data": {"document": {
            "revision_id": 42,
            "content": "# 世界观\n\n正文\n\n## 系统元数据（请勿编辑）\n```json\n{\"schema_version\":1,\"key\":\"海外绘本/小老鼠迈尔斯/worldview\",\"page_type\":\"worldview\",\"source_node_tokens\":[\"node-a\"],\"source_revisions\":{\"node-a\":\"17\"},\"last_ai_revision_id\":42}\n```\n",
        }}}


class OtherSeriesControlPlane:
    def read_index(self):
        return {
            "海外绘本/小老鼠迈尔斯/worldview": SimpleNamespace(
                key="海外绘本/小老鼠迈尔斯/worldview",
                doc_token="doc-a",
                wiki_node_token="wiki-a",
                source_revisions={"node-a": "17"},
                last_ai_revision_id=42,
                last_seen_revision_id=42,
                status="published",
            ),
            "其他系列/小老鼠迈尔斯/characters": SimpleNamespace(
                key="其他系列/小老鼠迈尔斯/characters",
                doc_token="doc-b",
                wiki_node_token="wiki-b",
                source_revisions={"node-b": "18"},
                last_ai_revision_id=43,
                last_seen_revision_id=43,
                status="published",
            ),
        }


class OtherSeriesCli:
    def fetch_doc(self, token):
        revision_by_token = {"doc-a": 42, "doc-b": 43}
        return {"data": {"document": {
            "revision_id": revision_by_token[token],
            "content": "# 世界观\n\n正文\n\n## 系统元数据（请勿编辑）\n```json\n{\"schema_version\":1,\"key\":\"海外绘本/小老鼠迈尔斯/worldview\",\"page_type\":\"worldview\",\"source_node_tokens\":[\"node-a\"],\"source_revisions\":{\"node-a\":\"17\"},\"last_ai_revision_id\":42}\n```\n",
        }}}


class AuthorityTests(unittest.TestCase):
    def test_missing_required_page_is_blocking(self):
        loader = AuthorityLoader(FakeControlPlane(), FakeCli())
        query = AuthorityQuery("小老鼠迈尔斯", "海外绘本", ("worldview", "characters"))
        with self.assertRaisesRegex(AuthorityGapError, "characters"):
            loader.load(query)

    def test_other_series_page_does_not_satisfy_required_page(self):
        loader = AuthorityLoader(OtherSeriesControlPlane(), OtherSeriesCli())
        query = AuthorityQuery("小老鼠迈尔斯", "海外绘本", ("worldview", "characters"))
        with self.assertRaisesRegex(AuthorityGapError, "characters"):
            loader.load(query)

    def test_page_key_mismatch_fails_closed(self):
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
        page = render_remote_page("# 世界观\n\n正文", {
            "key": "海外绘本/其他项目/worldview",
            "page_type": "worldview",
            "source_node_tokens": ["node-a"],
            "source_revisions": {"node-a": "17"},
            "last_ai_revision_id": 42,
        })
        loader = AuthorityLoader(_StaticIndexPlane(index), _StaticPageCli({"doc-a": (42, page)}))
        with self.assertRaisesRegex(ValueError, "系统元数据与索引不一致"):
            loader.load(AuthorityQuery("小老鼠迈尔斯", "海外绘本", ("worldview",)))

    def test_page_source_vector_mismatch_fails_closed(self):
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
        page = render_remote_page("# 世界观\n\n正文", {
            "key": "海外绘本/小老鼠迈尔斯/worldview",
            "page_type": "worldview",
            "source_node_tokens": ["node-b"],
            "source_revisions": {"node-b": "18"},
            "last_ai_revision_id": 42,
        })
        loader = AuthorityLoader(_StaticIndexPlane(index), _StaticPageCli({"doc-a": (42, page)}))
        with self.assertRaisesRegex(ValueError, "系统元数据与索引不一致"):
            loader.load(AuthorityQuery("小老鼠迈尔斯", "海外绘本", ("worldview",)))


class _StaticIndexPlane:
    def __init__(self, index):
        self.index = index

    def read_index(self):
        return self.index


class _StaticPageCli:
    def __init__(self, pages):
        self.pages = pages

    def fetch_doc(self, token):
        revision, content = self.pages[token]
        return {"data": {"document": {"revision_id": revision, "content": content}}}


class RecordingCache:
    def __init__(self):
        self.saved = None

    def save(self, bundle):
        self.saved = bundle


class AuthorityCacheTests(unittest.TestCase):
    def test_successful_remote_load_saves_cache(self):
        cache = RecordingCache()
        loader = AuthorityLoader(FakeControlPlane(), FakeCli(), cache_store=cache)
        bundle = loader.load(AuthorityQuery("小老鼠迈尔斯", "海外绘本", ("worldview",)))
        self.assertFalse(bundle.offline)
        self.assertTrue(bundle.items[0].index_synced)
        self.assertEqual(cache.saved["items"][0]["key"], "海外绘本/小老鼠迈尔斯/worldview")

    def test_unsynced_remote_load_does_not_replace_cache(self):
        index = {
            "海外绘本/小老鼠迈尔斯/worldview": IndexEntry(
                key="海外绘本/小老鼠迈尔斯/worldview",
                doc_token="doc-a",
                wiki_node_token="node-a",
                source_revisions={"node-a": "17"},
                last_ai_revision_id=42,
                last_seen_revision_id=41,
                status="published",
            )
        }
        page = render_remote_page("# 世界观\n\n人工更新正文", {
            "key": "海外绘本/小老鼠迈尔斯/worldview",
            "page_type": "worldview",
            "source_node_tokens": ["node-a"],
            "source_revisions": {"node-a": "17"},
            "last_ai_revision_id": 42,
        })
        cache = RecordingCache()
        loader = AuthorityLoader(
            _StaticIndexPlane(index),
            _StaticPageCli({"doc-a": (42, page)}),
            cache_store=cache,
        )

        bundle = loader.load(AuthorityQuery("小老鼠迈尔斯", "海外绘本", ("worldview",)))

        self.assertFalse(bundle.items[0].index_synced)
        self.assertIsNone(cache.saved)

    def test_remote_failure_uses_cache_only_with_explicit_opt_in(self):
        class FailingPlane:
            def read_index(self):
                raise RuntimeError("network down")

        item = KnowledgeEvidence(
            key="海外绘本/小老鼠迈尔斯/worldview", doc_token="doc-a",
            revision_id=42, title="worldview", content="正文",
            source_revisions={"node-a": "17"},
        )
        cached = KnowledgeEvidenceBundle(
            items=(item,), warnings=("缓存",), offline=False,
            fetched_at="2026-09-20T10:00:00+08:00",
        )
        loader = AuthorityLoader(FailingPlane(), FakeCli(), cached_bundle=cached)
        bundle = loader.load(
            AuthorityQuery("小老鼠迈尔斯", "海外绘本", ("worldview",)),
            allow_offline_cache=True,
        )
        self.assertTrue(bundle.offline)
        self.assertIn("非权威", bundle.warnings[0])

        strict = AuthorityLoader(FailingPlane(), FakeCli(), cached_bundle=cached)
        with self.assertRaisesRegex(RuntimeError, "network down"):
            strict.load(AuthorityQuery("小老鼠迈尔斯", "海外绘本", ("worldview",)))

    def test_authority_gap_does_not_replace_existing_cache(self):
        class EmptyPlane:
            def read_index(self):
                return {}

        cache = RecordingCache()
        loader = AuthorityLoader(EmptyPlane(), FakeCli(), cache_store=cache)
        with self.assertRaises(AuthorityGapError):
            loader.load(AuthorityQuery("小老鼠迈尔斯", "海外绘本", ("worldview",)))
        self.assertIsNone(cache.saved)


if __name__ == "__main__":
    unittest.main()
