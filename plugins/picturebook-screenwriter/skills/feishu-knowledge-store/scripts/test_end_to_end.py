"""Offline real Publisher + SyncRunner page/index handoff regression."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_publisher import FakeCli
from test_sync_runner import KEY, Plane, entry
from publisher import Publisher
from page_codec import render_remote_page
from source_admission import AdmissionEntry, AdmissionPolicy
from source_baseline import build_source_baseline
from sync_runner import SyncRunner, SyncRunnerError

WIKI_SCRIPTS = Path(__file__).resolve().parents[2] / "wiki-ingest" / "scripts"
if str(WIKI_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(WIKI_SCRIPTS))
import check_delta as cd  # noqa: E402

class EndToEndTests(unittest.TestCase):
    def test_confirmed_overwrite_failed_index_then_remote_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            staging = run / 'wiki_staging'
            staging.mkdir()
            (staging / 'page.md').write_text('---\ntitle: test\nseries_id: s\nproject_id: p\npage_type: worldview\nsource_node_tokens:\n  - src\nsource_revision_parts:\n  - "1"\n---\nAI content\n', encoding='utf-8')
            (staging / '_manifest.json').write_text(json.dumps({'entries': [{'key': KEY, 'path': 'page.md'}]}), encoding='utf-8')
            cli = FakeCli()
            cli.docs['doc'] = {'revision_id': 8, 'content': render_remote_page('human content', Publisher._metadata(entry()))}
            publisher = Publisher(cli, 'root', 'space')
            publisher.tokens = {'content': 'content'}
            plane = Plane()
            plane.fail = True
            runner = SyncRunner(None, cli, publisher, plane)
            with self.assertRaises(SyncRunnerError): runner.publish(run)
            report = json.loads((run / 'sync_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['overwritten_human_edits'], [KEY])
            self.assertTrue(report['pages'][0]['page_overwritten'])
            self.assertEqual(report['published'], 0)
            written_revision = cli.docs['doc']['revision_id']
            plane.fail = False
            report = runner.publish(run)
            self.assertEqual(report['preserved'], 1)
            self.assertEqual(report['overwritten_human_edits'], [])
            self.assertEqual(cli.docs['doc']['revision_id'], written_revision)
            self.assertEqual(plane.index[KEY].last_seen_revision_id, written_revision)
            self.assertEqual(plane.released, 2)

    def test_source_baseline_admission_to_delta_contract(self):
        indexed = entry()
        indexed = type(indexed)(
            key=indexed.key, doc_token=indexed.doc_token,
            wiki_node_token=indexed.wiki_node_token,
            source_revisions=indexed.source_revisions,
            last_ai_revision_id=indexed.last_ai_revision_id,
            last_seen_revision_id=indexed.last_seen_revision_id,
            status=indexed.status, source_edit_times={"src": 1756572300000},
        )
        policy = AdmissionPolicy(entries=(
            AdmissionEntry(
                token="container", title="容器", decision="exclude",
                decided_by="user", decided_at="2026-09-23T10:00:00+08:00",
                reason="排除",
            ),
        ), revision_id=3, page_present=True)
        baseline = build_source_baseline(9, {indexed.key: indexed}, policy)
        snapshot = {"nodes": {
            "root": {"parent_node_token": ""},
            "container": {"parent_node_token": "root", "has_child": True},
            "child": {"parent_node_token": "container"},
            "src": {
                "parent_node_token": "root", "edit_time_ms": 1756572300000,
                "revision_id": "r1", "obj_type": "file",
            },
        }}
        result = cd.classify(snapshot, baseline)
        self.assertEqual(result["verdicts"]["src"]["verdict"], "unchanged")
        self.assertEqual(result["verdicts"]["container"]["verdict"], "excluded")
        self.assertEqual(result["verdicts"]["child"]["verdict"], "excluded")
        self.assertEqual(result["summary"]["excluded"], 2)

if __name__ == '__main__': unittest.main()
