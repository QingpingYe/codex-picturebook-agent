import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from authority_cli import main
from page_codec import render_remote_page


class FakePlane:
    def read_index(self):
        from models import IndexEntry
        return {
            "海外绘本/小老鼠迈尔斯/worldview": IndexEntry(
                key="海外绘本/小老鼠迈尔斯/worldview", doc_token="doc-world",
                wiki_node_token="node-world", source_revisions={"source": "r1"},
                last_ai_revision_id=42, last_seen_revision_id=42,
                status="published",
            )
        }


class FakeCli:
    def fetch_doc(self, token):
        content = render_remote_page("# 世界观\n\n正文", {
            "key": "海外绘本/小老鼠迈尔斯/worldview", "page_type": "worldview",
            "source_node_tokens": ["source"],
            "source_revisions": {"source": "r1"},
            "last_ai_revision_id": 42,
        })
        return {"data": {"document": {"revision_id": 42, "content": content}}}


def valid_config(path: Path):
    path.write_text(json.dumps({
        "schema_version": 2,
        "source": {"space_id": "source-space", "root_mode": "space",
                   "wiki_url": "https://example.feishu.cn/wiki/source"},
        "target": {"space_id": "target-space", "root_token": "root-token"},
        "identity": "user", "lock_ttl_minutes": 45,
    }), encoding="utf-8")


class AuthorityCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config_path = self.root / "feishu-knowledge-base.json"
        valid_config(self.config_path)

    def test_load_returns_remote_evidence_and_writes_cache(self):
        def factory(config_path, environ=None, workspace=None):
            return SimpleNamespace(
                config=SimpleNamespace(cli_candidates=("lark-cli",)),
                cli=FakeCli(), publisher=None, control_plane=FakePlane(),
            )

        stdout = StringIO()
        exit_code = main([
            "load", "--project-id", "小老鼠迈尔斯", "--series-id", "海外绘本",
            "--page-types", "worldview", "--workspace", str(self.root),
            "--config", str(self.config_path),
        ], stdout=stdout, components_factory=factory, cli_probe=lambda **kwargs: {
            "status": "available", "version": "1.0.95",
        }, getuser=lambda: "tester")
        payload = json.loads(stdout.getvalue())
        cache = self.root / ".picturebook-screenwriter" / "cache" / "tester" / "knowledge-bundle.json"
        self.assertEqual(exit_code, 0)
        self.assertFalse(payload["offline"])
        self.assertEqual(payload["items"][0]["key"], "海外绘本/小老鼠迈尔斯/worldview")
        self.assertTrue(cache.exists())

    def test_remote_failure_requires_explicit_opt_in(self):
        def factory(config_path, environ=None, workspace=None):
            raise RuntimeError("network down")

        stdout = StringIO()
        exit_code = main([
            "load", "--project-id", "p", "--series-id", "s",
            "--page-types", "worldview", "--workspace", str(self.root),
            "--config", str(self.config_path),
        ], stdout=stdout, components_factory=factory,
            cli_probe=lambda **kwargs: {"status": "available"})
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["status"], "runtime_error")

    def test_missing_config_is_actionable(self):
        empty_workspace = self.root / "empty-workspace"
        empty_workspace.mkdir()
        stdout = StringIO()
        exit_code = main([
            "load", "--project-id", "p", "--series-id", "s",
            "--page-types", "worldview", "--workspace", str(empty_workspace),
        ], stdout=stdout, components_factory=lambda *args, **kwargs: None)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["status"], "missing_config")
        self.assertIn("searched", payload)


if __name__ == "__main__":
    unittest.main()
