import json
import sys
import tempfile
import unittest
from control_plane import ControlPlaneCorrupt
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
        self.refreshed = 0

    def acquire_lock(self, holder, now):
        self.acquired += 1
        return object()

    def release_lock(self, lease):
        self.released += 1

    def refresh_lock(self, lease, now):
        self.refreshed += 1
        return object()

    def read_index(self):
        return {}

    def update_index(self, entries):
        return {entry.key: entry for entry in entries}


class RecordingPlane(FakeControlPlane):
    def __init__(self):
        super().__init__()
        self.updated = []

    def update_index(self, entries):
        self.updated.extend(entries)
        return {entry.key: entry for entry in self.updated}


class FakePublisher:
    def __init__(self):
        self.published = []
        self.initialized = False

    def initialize(self):
        self.initialized = True
        return {"content": "content-root"}

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
            "root_token": "root-token",
            },
            "identity": "user",
            "lock_ttl_minutes": 45
        }), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_manifest(self, count=1):
        staging = self.run_dir / "wiki_staging"
        staging.mkdir(parents=True)
        entries = []
        for number in range(count):
            suffix = "" if count == 1 else f"-{number}"
            project_id = "小老鼠迈尔斯" if count == 1 else f"小老鼠迈尔斯-{number}"
            candidate_name = f"worldview{suffix}.md"
            (staging / candidate_name).write_text(
                "---\n"
                "title: 世界观\n"
                "series_id: 海外绘本\n"
                f"project_id: {project_id}\n"
                "page_type: worldview\n"
                "source_node_tokens:\n  - node-a\n"
                "source_revision_parts:\n  - \"17\"\n"
                "---\n"
                "# 世界观\n",
                encoding="utf-8",
            )
            entries.append({
                "path": candidate_name,
                "key": f"海外绘本/{project_id}/worldview",
                "source_revisions": {"node-a": "17"},
                "page_type": "worldview",
                "series_id": "海外绘本",
                "project_id": project_id,
            })
        manifest = {
            "version": 9,
            "series": {},
            "root": [],
            "entries": entries,
        }
        (staging / "_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def test_prepare_writes_run_dir_only(self):
        self._write_manifest()
        runner = SyncRunner(self.config_path, FakeCli(), publisher=FakePublisher(),
                             control_plane=FakeControlPlane())
        runner.prepare(self.run_dir)
        self.assertTrue((self.run_dir / "source_nodes.json").exists())
        self.assertTrue((self.run_dir / "wiki_staging" / "_manifest.json").exists())

    def test_prepare_discovers_workspace_config_without_explicit_config(self):
        self._write_manifest()
        workspace_config = Path(self.tmp.name) / "feishu-knowledge-base.json"
        workspace_config.write_text(
            self.config_path.read_text(encoding="utf-8"), encoding="utf-8",
        )
        runner = SyncRunner(
            None, FakeCli(), publisher=FakePublisher(),
            control_plane=FakeControlPlane(),
            workspace=Path(self.tmp.name), environ={},
        )
        runner.prepare(self.run_dir)
        self.assertEqual(runner.config_path, workspace_config)

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

    def test_publish_uses_content_root_and_updates_index(self):
        self._write_manifest()
        plane = RecordingPlane()
        publisher = FakePublisher()
        publisher.initialize = lambda: {"content": "content-root"}
        runner = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane)
        report = runner.publish(self.run_dir)
        self.assertEqual(publisher.published[0][2], "content-root")
        self.assertEqual([entry.key for entry in plane.updated], [
            "海外绘本/小老鼠迈尔斯/worldview",
        ])
        self.assertEqual(report["published"], 1)

    def test_publish_renews_lease_every_five_successful_pages(self):
        self._write_manifest(5)
        plane = RecordingPlane()
        publisher = FakePublisher()
        runner = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane)
        report = runner.publish(self.run_dir)
        self.assertEqual(report["published"], 5)
        self.assertEqual(plane.refreshed, 1)

    def test_publish_does_not_create_bootstrap_page(self):
        self._write_manifest()
        publisher = FakePublisher()
        runner = SyncRunner(self.config_path, FakeCli(), publisher=publisher,
                            control_plane=FakeControlPlane())
        runner.publish(self.run_dir)
        self.assertFalse(any(key == "system/bootstrap" for key, *_ in publisher.published))

    def test_publish_reads_remote_index_before_writing(self):
        self._write_manifest()
        plane = RecordingPlane()
        plane.read_index = lambda: {}
        publisher = FakePublisher()
        runner = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane)
        runner.publish(self.run_dir)
        self.assertEqual(publisher.published[0][2], "content-root")
        self.assertEqual(plane.updated[0].key, "海外绘本/小老鼠迈尔斯/worldview")

    def test_corrupt_remote_index_blocks_all_writes(self):
        self._write_manifest()
        publisher = FakePublisher()

        class CorruptPlane(FakeControlPlane):
            def read_index(self):
                raise ControlPlaneCorrupt("invalid index schema")

        runner = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=CorruptPlane())
        with self.assertRaises(ControlPlaneCorrupt):
            runner.publish(self.run_dir)
        self.assertEqual(publisher.published, [])

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
