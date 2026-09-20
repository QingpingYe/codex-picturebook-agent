import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

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
        target=SimpleNamespace(root_token="root-token", space_id="target-space"),
    )


class FakeCli:
    def preflight(self, target_root_token):
        return {"identity": "user", "root": target_root_token}


class FakePublisher:
    def __init__(self, tokens=None, error=None):
        self.tokens = tokens or {
            "index": "index-doc", "lock": "lock-doc", "conflict": "conflict-doc",
        }
        self.error = error

    def resolve_control_plane(self):
        if self.error:
            raise self.error
        return self.tokens

    def fetch_current(self, doc_token):
        return {"revision_id": 3, "content": "# AI_KB_CONFLICT_QUEUE_V1\n"}


class FakeControlPlane:
    def read_lock(self):
        return 7, {"holder": None, "run_id": None}


def fake_factory(config_path, environ=None):
    return SimpleNamespace(
        config=config(Path(config_path)),
        cli=FakeCli(),
        publisher=FakePublisher(),
        control_plane=FakeControlPlane(),
    )


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


if __name__ == "__main__":
    unittest.main()
