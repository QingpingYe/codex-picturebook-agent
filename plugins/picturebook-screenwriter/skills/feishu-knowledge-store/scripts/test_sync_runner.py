import json
import sys
import tempfile
import unittest
from dataclasses import replace
from control_plane import ControlPlaneCorrupt
from pathlib import Path

SCRIPTS = Path(__file__).parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from models import IndexEntry
from runner_status import BootstrapState
from sync_runner import SyncRunner
from page_codec import render_remote_page
from publisher import NeedsReview


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
        return {"content": "content-root", "conflict": "conflict-doc"}

    def publish_new(self, entry, body, parent):
        self.published.append((entry.key, body, parent))
        return entry


class FakeCli:
    def list_nodes(self, space_id, parent_node_token=None, page_limit=10):
        return [{"title": "节点", "node_token": "node-1"}]


def indexed_entry():
    return IndexEntry(
        key="海外绘本/小老鼠迈尔斯/worldview",
        doc_token="doc-world", wiki_node_token="node-world",
        source_revisions={"node-a": "17"}, last_ai_revision_id=4,
        last_seen_revision_id=4, status="published",
    )


def indexed_remote_page(source_revision="17"):
    return render_remote_page("# 世界观\n", {
        "schema_version": 1,
        "key": "海外绘本/小老鼠迈尔斯/worldview",
        "page_type": "worldview",
        "source_node_tokens": ["node-a"],
        "source_revisions": {"node-a": source_revision},
        "last_ai_revision_id": 4,
    })


class IndexedPlane(FakeControlPlane):
    def __init__(self):
        super().__init__()
        self.index = {}
        self.updated = []

    def read_index(self):
        return self.index

    def update_index(self, entries):
        for item in entries:
            self.index[item.key] = item
            self.updated.append(item)
        return self.index


class RoutingPublisher(FakePublisher):
    def __init__(self):
        super().__init__()
        self.updated = []
        self.conflicts = []
        self.current = {"revision_id": 4, "content": indexed_remote_page()}
        self.history = {"revision_id": 4, "content": indexed_remote_page()}

    def fetch_current(self, doc_token):
        return self.current

    def fetch_revision(self, doc_token, revision_id):
        return self.history

    def conditional_update(self, entry, current, merged_markdown, source_revisions):
        self.updated.append((entry.key, merged_markdown, source_revisions))
        return replace(entry, source_revisions=dict(source_revisions),
                       last_ai_revision_id=current["revision_id"] + 2,
                       last_seen_revision_id=current["revision_id"] + 2)

    def append_conflict(self, parent, record):
        self.conflicts.append((parent, record))


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
                "# 世界观\n\n新增资料\n",
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

    def test_absent_index_key_is_first_published(self):
        self._write_manifest()
        plane = IndexedPlane()
        publisher = RoutingPublisher()
        runner = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane)
        report = runner.publish(self.run_dir)
        self.assertEqual(publisher.published[0][0], "海外绘本/小老鼠迈尔斯/worldview")
        self.assertEqual(report["published"], 1)

    def test_existing_key_with_same_source_vector_is_preserved(self):
        self._write_manifest()
        plane = IndexedPlane()
        plane.index["海外绘本/小老鼠迈尔斯/worldview"] = indexed_entry()
        publisher = RoutingPublisher()
        runner = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane)
        report = runner.publish(self.run_dir)
        self.assertEqual(publisher.published, [])
        self.assertEqual(publisher.updated, [])
        self.assertEqual(report["preserved"], 1)
        self.assertEqual(plane.updated[0].last_seen_revision_id, 4)

    def test_existing_key_with_changed_source_vector_is_updated(self):
        self._write_manifest()
        plane = IndexedPlane()
        plane.index["海外绘本/小老鼠迈尔斯/worldview"] = replace(
            indexed_entry(), source_revisions={"node-a": "16"}
        )
        publisher = RoutingPublisher()
        publisher.current = {"revision_id": 4, "content": indexed_remote_page("16")}
        publisher.history = {"revision_id": 4, "content": indexed_remote_page("16")}
        runner = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane)
        report = runner.publish(self.run_dir)
        self.assertEqual(publisher.published, [])
        self.assertEqual(publisher.updated[0][0], "海外绘本/小老鼠迈尔斯/worldview")
        self.assertEqual(report["published"], 1)

    def test_target_page_without_index_entry_is_queued(self):
        self._write_manifest()
        plane = IndexedPlane()
        publisher = RoutingPublisher()

        def existing_page(*args, **kwargs):
            raise NeedsReview("logical key page already exists")

        publisher.publish_new = existing_page
        runner = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane)
        report = runner.publish(self.run_dir)
        self.assertEqual(report["queued"], 1)
        self.assertEqual(report["published"], 0)
        self.assertEqual(publisher.conflicts[0][1]["key"], "海外绘本/小老鼠迈尔斯/worldview")

    def test_page_success_with_index_failure_is_queued(self):
        self._write_manifest()
        plane = IndexedPlane()

        def failed_update(entries):
            raise ControlPlaneCorrupt("index readback did not match")

        plane.update_index = failed_update
        publisher = RoutingPublisher()
        runner = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane)
        report = runner.publish(self.run_dir)
        self.assertEqual(report["queued"], 1)
        self.assertEqual(report["published"], 0)
        self.assertEqual(
            publisher.conflicts[0][1]["reason"],
            "index_update_failed: index readback did not match",
        )

    def test_missing_indexed_page_is_queued(self):
        self._write_manifest()
        plane = IndexedPlane()
        plane.index["海外绘本/小老鼠迈尔斯/worldview"] = indexed_entry()
        publisher = RoutingPublisher()

        def missing_page(doc_token):
            raise NeedsReview("missing current page")

        publisher.fetch_current = missing_page
        runner = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane)
        report = runner.publish(self.run_dir)
        self.assertEqual(report["queued"], 1)
        self.assertEqual(report["failed"], 0)

    def test_page_metadata_drift_from_index_is_queued(self):
        self._write_manifest()
        plane = IndexedPlane()
        plane.index["海外绘本/小老鼠迈尔斯/worldview"] = indexed_entry()
        publisher = RoutingPublisher()
        publisher.current = {"revision_id": 4, "content": indexed_remote_page("16")}
        runner = SyncRunner(self.config_path, FakeCli(), publisher=publisher, control_plane=plane)
        report = runner.publish(self.run_dir)
        self.assertEqual(report["queued"], 1)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(
            publisher.conflicts[0][1]["reason"],
            "remote page metadata does not match the remote index",
        )

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
