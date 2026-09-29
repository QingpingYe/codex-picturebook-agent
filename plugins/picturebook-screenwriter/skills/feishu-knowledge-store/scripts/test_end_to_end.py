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
from sync_runner import SyncRunner, SyncRunnerError

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

if __name__ == '__main__': unittest.main()
