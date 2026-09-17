import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from models import IndexEntry
from runner_status import BootstrapState
from sync_runner import SyncRunner


class FakeControlPlane:
    def __init__(self):
        self.acquired = 0
        self.released = 0

    def acquire_lock(self, holder, now):
        self.acquired += 1
        return object()

    def release_lock(self, lease):
        self.released += 1


class FakePublisher:
    def __init__(self):
        self.published = []
        self.initialized = False

    def initialize(self):
        self.initialized = True
        return {}

    def publish_new(self, entry, body, parent):
        self.published.append((entry.key, body, parent))
        return entry


class FakeCli:
    def list_nodes(self, space_id, parent_node_token=None, page_limit=10):
        return [{"title": "节点", "node_token": "node-1"}]


class SyncRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "runs" / "run-1"
        self.config_path = Path(self.tmp.name) / "config.json"
        self.config_path.write_text(json.dumps({
            "schema_version": 2,
            "source": {
                "space_id": "7682720271706361023",
                "root_mode": "space",
                "wiki_url": "https://example.feishu.cn/wiki/source",
            },
            "target": {
                "space_id": "7686313522543774944",
                "root_token": "T08vwqXroiuJEfkoVzFcRaFXnMf",
            },
            "identity": "user",
            "lock_ttl_minutes": 45
        }), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_manifest(self):
        staging = self.run_dir / "wiki_staging"
        staging.mkdir(parents=True)
        candidate = staging / "worldview.md"
        candidate.write_text(
            "---\n"
            "title: 世界观\n"
            "series_id: 海外绘本\n"
            "project_id: 小老鼠迈尔斯\n"
            "page_type: worldview\n"
            "source_node_tokens:\n  - node-a\n"
            "source_revision_parts:\n  - \"17\"\n"
            "---\n"
            "# 世界观\n",
            encoding="utf-8",
        )
        manifest = {
            "version": 9,
            "series": {},
            "root": [],
            "entries": [{
                "path": "worldview.md",
                "key": "海外绘本/小老鼠迈尔斯/worldview",
                "source_revisions": {"node-a": "17"},
                "page_type": "worldview",
                "series_id": "海外绘本",
                "project_id": "小老鼠迈尔斯",
            }],
        }
        (staging / "_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def test_prepare_writes_run_dir_only(self):
        self._write_manifest()
        runner = SyncRunner(self.config_path, FakeCli(), publisher=FakePublisher(),
                             control_plane=FakeControlPlane())
        runner.prepare(self.run_dir)
        self.assertTrue((self.run_dir / "source_nodes.json").exists())
        self.assertTrue((self.run_dir / "wiki_staging" / "_manifest.json").exists())

    def test_node_mode_with_no_children_is_rejected(self):
        self._write_manifest()
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        config["source"]["root_mode"] = "node"
        self.config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

        class EmptyCli:
            def list_nodes(self, space_id, parent_node_token=None, page_limit=10):
                return []

        runner = SyncRunner(self.config_path, EmptyCli(), publisher=FakePublisher(),
                             control_plane=FakeControlPlane())
        with self.assertRaisesRegex(RuntimeError, "no child nodes"):
            runner.prepare(self.run_dir)

    def test_publish_report_includes_run_id_and_source_summary(self):
        self._write_manifest()
        runner = SyncRunner(self.config_path, FakeCli(), publisher=FakePublisher(),
                             control_plane=FakeControlPlane())
        runner.prepare(self.run_dir)
        report = runner.publish(self.run_dir)
        self.assertEqual(report["run_id"], "run-1")
        self.assertEqual(report["source"], {"document_count": 1, "container_count": 0})

    def test_chinese_path_and_title_round_trip(self):
        self.run_dir = Path(self.tmp.name) / "中文运行目录" / "runs" / "run-1"
        self._write_manifest()
        runner = SyncRunner(self.config_path, FakeCli(), publisher=FakePublisher(),
                             control_plane=FakeControlPlane())
        runner.prepare(self.run_dir)
        self.assertTrue((self.run_dir / "source_nodes.json").exists())

    def test_publish_acquires_and_releases_lock(self):
        self._write_manifest()
        plane = FakeControlPlane()
        publisher = FakePublisher()
        runner = SyncRunner(self.config_path, FakeCli(), publisher=publisher,
                             control_plane=plane)
        runner.publish(self.run_dir)
        self.assertEqual(plane.acquired, 1)
        self.assertEqual(plane.released, 1)
        self.assertTrue(publisher.published)

    def test_verify_is_read_only(self):
        self._write_manifest()
        runner = SyncRunner(self.config_path, FakeCli(), publisher=FakePublisher(),
                             control_plane=FakeControlPlane())
        report = runner.verify(self.run_dir)
        self.assertEqual(report["status"], "verified")
        self.assertTrue((self.run_dir / "verify_report.json").exists())

    def test_bootstrap_state_can_resume_after_failure(self):
        self._write_manifest()
        publisher = FakePublisher()
        publisher.publish_new = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
        plane = FakeControlPlane()
        runner = SyncRunner(self.config_path, FakeCli(), publisher=publisher,
                             control_plane=plane)
        with self.assertRaises(RuntimeError):
            runner.publish(self.run_dir)
        self.assertEqual(plane.released, 1)
        self.assertEqual(runner.bootstrap_state, BootstrapState.FAILED)


if __name__ == "__main__":
    unittest.main()
