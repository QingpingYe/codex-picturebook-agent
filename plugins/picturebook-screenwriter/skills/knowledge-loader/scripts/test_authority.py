import unittest
from types import SimpleNamespace

from authority import AuthorityLoader, AuthorityQuery, AuthorityGapError


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
        return {"data": {"document": {
            "revision_id": 43,
            "content": "# 角色\n\n正文\n\n## 系统元数据（请勿编辑）\n```json\n{\"schema_version\":1,\"key\":\"其他系列/小老鼠迈尔斯/characters\",\"page_type\":\"characters\",\"source_node_tokens\":[\"node-b\"],\"source_revisions\":{\"node-b\":\"18\"},\"last_ai_revision_id\":43}\n```\n",
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


if __name__ == "__main__":
    unittest.main()
