import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from models import IndexEntry

SCRIPTS = Path(__file__).parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import store_cli


def write_config(path: Path) -> None:
    path.write_text(json.dumps({
        "schema_version": 2,
        "source": {
            "space_id": "source-space",
            "root_mode": "space",
            "wiki_url": "https://example.feishu.cn/wiki/source",
        },
        "target": {"space_id": "target-space", "root_token": "root-token"},
        "identity": "user",
        "lock_ttl_minutes": 45,
    }), encoding="utf-8")


def config(path: Path):
    return SimpleNamespace(
        identity="user",
        lock_ttl_minutes=45,
        source=SimpleNamespace(space_id="preloaded-source", root_mode="space"),
        target=SimpleNamespace(root_token="root-token", space_id="target-space"),
    )


class FakeCli:
    def __init__(self):
        self.listed = []
        self.fetched = []

    def list_nodes(self, space_id, parent_node_token=None, page_limit=10):
        self.listed.append(space_id)
        if parent_node_token == "root-token":
            return [{"node_token": "node-page", "title": "s/p/worldview"}]
        return []

    def preflight(self, target_root_token):
        return {"identity": "user", "root": target_root_token}

    def fetch_doc(self, doc_token):
        self.fetched.append(doc_token)
        content_by_token = {
            "index-doc": "# AI_KB_INDEX_V1\n```json\n{\"schema_version\":1,\"entries\":[]}\n```\n",
            "conflict-doc": "# AI_KB_CONFLICT_QUEUE_V1\n",
            "page-doc": "# 世界观\n\n正文\n",
        }
        revision_by_token = {"index-doc": 2, "conflict-doc": 3, "page-doc": 4}
        return {"data": {"document": {
            "revision_id": revision_by_token[doc_token],
            "content": content_by_token[doc_token],
        }}}


class FakePublisher:
    def __init__(self, tokens=None, error=None):
        self.tokens = tokens or {
            "index": "index-doc", "lock": "lock-doc", "conflict": "conflict-doc",
        }
        self.error = error
        self.appended = []

    def resolve_control_plane(self):
        if self.error:
            raise self.error
        return self.tokens

    def fetch_current(self, doc_token):
        return {"revision_id": 3, "content": "# AI_KB_CONFLICT_QUEUE_V1\n"}

    def append_conflict(self, parent, record):
        self.appended.append((parent, record))
        return (parent, record)


class FakeControlPlane:
    def __init__(self):
        self.acquired = []
        self.released = []

    def acquire_lock(self, holder, now):
        self.acquired.append(holder)
        return "lease"

    def release_lock(self, lease):
        self.released.append(lease)

    def read_lock(self):
        return 7, {"holder": None, "run_id": None}

    def read_index(self):
        return {
            "s/p/worldview": IndexEntry(
                key="s/p/worldview", doc_token="page-doc",
                wiki_node_token="node-page", source_revisions={"node": "1"},
                last_ai_revision_id=4, last_seen_revision_id=4,
                status="published",
            )
        }


def fake_factory(config_path, environ=None):
    return SimpleNamespace(
        config=config(Path(config_path)),
        cli=FakeCli(),
        publisher=FakePublisher(),
        control_plane=FakeControlPlane(),
    )


def write_manifest(run_dir: Path) -> None:
    staging = run_dir / "wiki_staging"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "worldview.md").write_text("---\ntitle: 世界观\n---\n# 世界观\n", encoding="utf-8")
    (staging / "_manifest.json").write_text(json.dumps({
        "version": 9, "series": {}, "root": [],
        "entries": [{
            "path": "worldview.md", "key": "s/p/worldview",
            "source_revisions": {"node": "1"}, "page_type": "worldview",
            "series_id": "s", "project_id": "p",
        }],
    }), encoding="utf-8")


class StoreCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = Path(self.tmp.name) / "config.json"
        write_config(self.config_path)

    def test_resolve_command_prints_control_tokens(self):
        stdout = StringIO()
        exit_code = store_cli.main(
            ["resolve", "--config", str(self.config_path)],
            stdout=stdout,
            components_factory=fake_factory,
        )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["index"], "index-doc")

    def test_preflight_uses_user_identity_and_reports_root(self):
        stdout = StringIO()
        exit_code = store_cli.main(
            ["preflight", "--config", str(self.config_path)],
            stdout=stdout,
            components_factory=fake_factory,
        )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["identity"], "user")
        self.assertEqual(payload["root"], "root-token")

    def test_lock_status_reads_public_lock_reader(self):
        stdout = StringIO()
        exit_code = store_cli.main(
            ["lock-status", "--config", str(self.config_path)],
            stdout=stdout,
            components_factory=fake_factory,
        )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["revision_id"], 7)

    def test_conflict_list_fetches_conflict_document(self):
        stdout = StringIO()
        exit_code = store_cli.main(
            ["conflict-list", "--config", str(self.config_path)],
            stdout=stdout,
            components_factory=fake_factory,
        )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["revision_id"], 3)

    def test_missing_control_page_fails_closed(self):
        class MissingPublisher(FakePublisher):
            def resolve_control_plane(self):
                raise RuntimeError("missing system page: AI_KB_INDEX_V1")

        def missing_factory(config_path, environ=None):
            return SimpleNamespace(
                config=config(Path(config_path)), cli=FakeCli(),
                publisher=MissingPublisher(), control_plane=FakeControlPlane(),
            )

        stdout = StringIO()
        exit_code = store_cli.main(
            ["resolve", "--config", str(self.config_path)],
            stdout=stdout,
            components_factory=missing_factory,
        )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertIn("missing system page", payload["error"])

    def test_prepare_command_delegates_to_sync_runner(self):
        run_dir = Path(self.tmp.name) / "run-1"
        write_manifest(run_dir)
        cli = FakeCli()

        def factory(config_path, environ=None):
            return SimpleNamespace(
                config=config(Path(config_path)), cli=cli,
                publisher=FakePublisher(), control_plane=FakeControlPlane(),
            )

        stdout = StringIO()
        exit_code = store_cli.main([
            "prepare", "--config", str(self.config_path), "--run-dir", str(run_dir),
        ], stdout=stdout, components_factory=factory)
        manifest = run_dir / "wiki_staging" / "_manifest.json"
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, str(manifest))
        self.assertTrue((run_dir / "source_nodes.json").exists())
        self.assertEqual(cli.listed, ["preloaded-source"])

    def test_conflict_append_acquires_lease_and_records_conflict(self):
        plane = FakeControlPlane()
        publisher = FakePublisher()

        def factory(config_path, environ=None):
            return SimpleNamespace(
                config=config(Path(config_path)), cli=FakeCli(),
                publisher=publisher, control_plane=plane,
            )

        stdout = StringIO()
        exit_code = store_cli.main([
            "conflict-append", "--config", str(self.config_path),
            "--key", "s/p/worldview", "--reason", "human conflict", "--holder", "human@example",
        ], stdout=stdout, components_factory=factory)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, {"key": "s/p/worldview", "reason": "human conflict"})
        self.assertEqual(publisher.appended, [("conflict-doc", payload)])
        self.assertEqual(plane.acquired, ["human@example"])
        self.assertEqual(plane.released, ["lease"])

    def test_conflict_append_without_holder_does_not_write(self):
        plane = FakeControlPlane()
        publisher = FakePublisher()

        def factory(config_path, environ=None):
            return SimpleNamespace(
                config=config(Path(config_path)), cli=FakeCli(),
                publisher=publisher, control_plane=plane,
            )

        exit_code = store_cli.main([
            "conflict-append", "--config", str(self.config_path),
            "--key", "s/p/worldview", "--reason", "human conflict",
        ], stdout=StringIO(), components_factory=factory)
        self.assertEqual(exit_code, 2)
        self.assertEqual(plane.acquired, [])
        self.assertEqual(publisher.appended, [])

    def test_lint_fixture_writes_tree_index_conflict_and_pages(self):
        cli = FakeCli()

        def factory(config_path, environ=None):
            return SimpleNamespace(
                config=config(Path(config_path)), cli=cli,
                publisher=FakePublisher(), control_plane=FakeControlPlane(),
            )

        out = Path(self.tmp.name) / "fixture"
        stdout = StringIO()
        exit_code = store_cli.main([
            "lint-fixture", "--config", str(self.config_path), "--out", str(out),
        ], stdout=stdout, components_factory=factory)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["out"], str(out))
        self.assertTrue((out / "tree.json").exists())
        self.assertTrue((out / "index.md").exists())
        self.assertTrue((out / "conflict.md").exists())
        self.assertTrue((out / "pages" / "s__p__worldview.md").exists())
        self.assertEqual(cli.fetched, ["index-doc", "conflict-doc", "page-doc"])


if __name__ == "__main__":
    unittest.main()
