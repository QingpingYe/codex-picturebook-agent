import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
from models import IndexEntry
from page_codec import parse_remote_page, render_remote_page
from publisher import NeedsReview, Publisher
from lark_cli import RevisionConflict
from control_plane import IndexOutcomeUnknown
from sync_runner import SyncRunner, SyncRunnerError

KEY = 's/p/worldview'

def entry(**changes):
    return replace(IndexEntry(KEY, 'doc', 'node', {'src': '1'}, 4, 4, 'published'), **changes)

class Plane:
    def __init__(self, indexed=True):
        self.index = {KEY: entry()} if indexed else {}
        self.fail = False
        self.released = 0
    def acquire_lock(self, *args): return 'lease'
    def release_lock(self, *args): self.released += 1
    def refresh_lock(self, *args): return 'lease'
    def read_index(self): return dict(self.index)
    def update_index(self, entries):
        if self.fail: raise RuntimeError('index unavailable')
        self.index.update({e.key: e for e in entries})
        return dict(self.index)

class Pages:
    def __init__(self, body='old', indexed=None):
        self.indexed = indexed or entry()
        self.current = {'revision_id': 4, 'content': render_remote_page(body, Publisher._metadata(self.indexed))}
        self.writes = 0
        self.reads = 0
        self.conflict = False
        self.orphan = False
    def initialize(self): return {'content': 'root'}
    def fetch_current(self, token):
        self.reads += 1
        return dict(self.current)
    def conditional_update(self, indexed, current, markdown, source_revisions, source_edit_times=None):
        if self.conflict:
            self.conflict = False
            self.current['revision_id'] += 1
            raise RevisionConflict('changed')
        self.writes += 1
        result = replace(indexed, source_revisions=source_revisions, source_edit_times=source_edit_times,
                         last_ai_revision_id=current['revision_id'] + 2,
                         last_seen_revision_id=current['revision_id'] + 3, status='published')
        self.current = {'revision_id': result.last_seen_revision_id,
                        'content': render_remote_page(parse_remote_page(markdown).body, Publisher._metadata(result))}
        return result
    def publish_new(self, candidate, body, parent):
        if self.orphan: raise NeedsReview('logical key page already exists')
        return self.conditional_update(candidate, {'revision_id': 0},
            render_remote_page(body, Publisher._metadata(candidate)), candidate.source_revisions, candidate.source_edit_times)

class SyncRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        self.plane, self.pages = Plane(), Pages()
    def candidate(self, body='new', revision='1', times=None, token='src', key=KEY):
        staging = self.run / 'wiki_staging'
        staging.mkdir(exist_ok=True)
        time_field = '' if times is None else f'source_edit_time_parts:\n  - {times}\n'
        text = ('---\ntitle: test\nseries_id: s\nproject_id: p\npage_type: worldview\n'
                f'source_node_tokens:\n  - {token}\nsource_revision_parts:\n  - "{revision}"\n{time_field}---\n{body}\n')
        (staging / 'page.md').write_text(text, encoding='utf-8')
        (staging / '_manifest.json').write_text(json.dumps({'entries': [{'key': key, 'path': 'page.md'}]}), encoding='utf-8')
    def publish(self):
        return SyncRunner(None, None, self.pages, self.plane).publish(self.run)
    def failure(self):
        with self.assertRaises(SyncRunnerError): self.publish()
        return json.loads((self.run / 'sync_report.json').read_text(encoding='utf-8'))
    def test_same_source_changed_body_publishes(self):
        self.candidate()
        self.assertEqual(self.publish()['published'], 1)
        self.assertEqual(self.pages.writes, 1)
    def test_format_only_and_changed_source_preserve(self):
        self.pages = Pages('# **same**')
        self.candidate('same', revision='2')
        result = self.publish()
        self.assertEqual(result['preserved'], 1)
        self.assertEqual(self.pages.writes, 0)
        self.assertEqual(self.plane.index[KEY].source_revisions, {'src': '1'})
    def test_literal_code_url_and_punctuation_changes_publish(self):
        for old, new in [('a*b', 'ab'), ('`a_b`', '`ab`'), ('[x](https://a)', '[x](https://b)'), ('hello!', 'hello?')]:
            with self.subTest(old=old):
                self.plane, self.pages = Plane(), Pages(old)
                self.candidate(new)
                self.assertEqual(self.publish()['published'], 1)
    def test_new_page_and_orphan(self):
        self.plane = Plane(False)
        self.candidate()
        self.assertEqual(self.publish()['published'], 1)
        self.plane = Plane(False)
        self.pages.orphan = True
        self.assertEqual(self.failure()['failed'], 1)
    def test_archived_is_not_changed(self):
        self.plane.index[KEY] = entry(status='archived')
        self.candidate()
        self.failure()
        self.assertEqual(self.plane.index[KEY].status, 'archived')
        self.assertEqual(self.pages.writes, 0)
    def test_report_and_disclosure(self):
        self.pages.current['revision_id'] = 8
        self.candidate()
        report = self.publish()
        self.assertEqual(report['overwritten_human_edits'], [KEY])
        self.assertFalse({'queued', 'conflicts', 'third_party_edits'} & report.keys())
        self.assertEqual(set(report['pages'][0]), {'key', 'action', 'reason', 'before_revision', 'after_revision', 'page_overwritten', 'index_committed'})
        self.assertEqual(report['pages'][0]['after_revision'], 11)
    def test_preserve_does_not_disclose_overwrite(self):
        self.pages.current['revision_id'] = 8
        self.candidate('old')
        self.assertEqual(self.publish()['overwritten_human_edits'], [])
    def test_revision_conflict_refetches_then_redecides(self):
        self.pages.conflict = True
        self.candidate()
        report = self.publish()
        self.assertEqual(report['retried'], 1)
        self.assertEqual(self.pages.reads, 2)
        self.assertEqual(report['pages'][0]['before_revision'], 5)
    def test_index_failure_reports_then_recovers_without_page_write(self):
        self.pages.current['revision_id'] = 8
        self.plane.fail = True
        self.candidate()
        report = self.failure()
        self.assertEqual(report['published'], 0)
        self.assertEqual(report['overwritten_human_edits'], [KEY])
        self.assertTrue(report['pages'][0]['page_overwritten'])
        self.assertFalse(report['pages'][0]['index_committed'])
        self.plane.fail = False
        self.assertEqual(self.publish()['preserved'], 1)
        self.assertEqual(self.pages.writes, 1)
        self.assertEqual(self.plane.index[KEY].last_seen_revision_id, 11)
    def test_recovery_rejects_wrong_content_or_vector(self):
        self.pages = Pages('new', entry(last_ai_revision_id=6, source_revisions={'src': '2'}))
        self.pages.current['revision_id'] = 7
        self.candidate('new')
        self.assertEqual(self.failure()['failed'], 1)
        self.assertEqual(self.pages.writes, 0)
    def test_needs_review_is_retryable(self):
        self.plane.index[KEY] = entry(status='needs_review')
        self.candidate('old')
        self.assertEqual(self.publish()['preserved'], 1)
        self.assertEqual(self.plane.index[KEY].status, 'published')
    def test_preserve_refreshes_times_without_source_vector(self):
        self.plane.index[KEY] = entry(source_edit_times={'src': 100})
        self.candidate('old', revision='2', times=200)
        self.publish()
        self.assertEqual(self.plane.index[KEY].source_edit_times, {'src': 200})
        self.assertEqual(self.plane.index[KEY].source_revisions, {'src': '1'})
        self.assertEqual(self.pages.writes, 0)
    def test_stale_time_blocks_preserve_and_publish(self):
        for body in ('old', 'new'):
            self.plane.index[KEY] = entry(source_edit_times={'src': 200})
            self.candidate(body, times=100)
            self.assertIn('stale', str(self.failure()))
        self.assertEqual(self.pages.writes, 0)
    def test_missing_times_preserves_or_downgrades_baseline(self):
        self.plane.index[KEY] = entry(source_edit_times={'src': 100})
        self.candidate('new')
        self.publish()
        self.assertEqual(self.plane.index[KEY].source_edit_times, {'src': 100})
        self.candidate('newer', revision='2')
        self.assertIn('baseline', str(self.publish()))
        self.assertIsNone(self.plane.index[KEY].source_edit_times)
    def test_changed_topology_preserve_keeps_old_baseline(self):
        self.plane.index[KEY] = entry(source_edit_times={'src': 100})
        self.candidate('old', times=200, token='other')
        self.assertIn('topology', str(self.publish()))
        self.assertEqual(self.plane.index[KEY].source_edit_times, {'src': 100})
    def test_uncertain_write_stops_remaining_candidates(self):
        self.candidate()
        path = self.run / 'wiki_staging' / '_manifest.json'
        manifest = json.loads(path.read_text())
        manifest['entries'].append({'key': 's/other/worldview', 'path': 'page.md'})
        path.write_text(json.dumps(manifest))
        def uncertain(*args, **kwargs): raise NeedsReview('update outcome is uncertain')
        self.pages.conditional_update = uncertain
        report = self.failure()
        self.assertIsNone(report['pages'][0]['page_overwritten'])
        self.assertIn('s/other/worldview', str(report['errors']))
        self.assertEqual(len(report['pages']), 1)

    def test_confirmed_write_is_disclosed_when_correction_fails(self):
        self.pages.current['revision_id'] = 8
        self.candidate()

        def confirmed_then_failed(*args, **kwargs):
            raise NeedsReview('confirmed page write followed by a failed metadata correction',
                              page_written=True, after_revision=11)

        self.pages.conditional_update = confirmed_then_failed
        report = self.failure()
        page = report['pages'][0]
        self.assertTrue(page['page_overwritten'])
        self.assertEqual(page['after_revision'], 11)
        self.assertEqual(report['overwritten_human_edits'], [KEY])

    def test_unknown_index_outcome_is_reported_as_null(self):
        class UnknownIndexPlane(Plane):
            def __init__(self):
                super().__init__()
                self.committed = False

            def read_index(self):
                if self.committed:
                    raise IndexOutcomeUnknown('index readback unavailable')
                return dict(self.index)

            def update_index(self, entries):
                result = super().update_index(entries)
                self.committed = True
                return result

        self.plane = UnknownIndexPlane()
        self.candidate()
        report = self.failure()
        self.assertTrue(report['pages'][0]['page_overwritten'])
        self.assertIsNone(report['pages'][0]['index_committed'])

    def test_invalid_run_input_still_writes_a_failed_report(self):
        with self.assertRaises(SyncRunnerError):
            self.publish()
        report = json.loads((self.run / 'sync_report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['failed'], 1)
        self.assertTrue(report['errors'])

    def test_conflict_redecides_to_preserve(self):
        self.candidate('new')
        def conflict(*args, **kwargs):
            self.pages.current = {'revision_id': 5, 'content': render_remote_page('**new**', Publisher._metadata(entry()))}
            raise RevisionConflict('changed')
        self.pages.conditional_update = conflict
        report = self.publish()
        self.assertEqual(report['retried'], 1)
        self.assertEqual(report['preserved'], 1)
        self.assertEqual(report['overwritten_human_edits'], [])

    def test_conflict_exhaustion_is_retryable_review(self):
        self.candidate()
        def conflict(*args, **kwargs): raise RevisionConflict('changed')
        self.pages.conditional_update = conflict
        report = self.failure()
        self.assertEqual(report['retried'], 2)
        self.assertFalse(report['pages'][0]['page_overwritten'])
        self.assertEqual(self.plane.index[KEY].status, 'needs_review')
        self.assertEqual(report['overwritten_human_edits'], [])

    def test_resource_and_metadata_drift_fail_without_write(self):
        for body, metadata in [('old <!-- comment -->', Publisher._metadata(entry())),
                               ('new', Publisher._metadata(entry(last_ai_revision_id=3)))]:
            self.plane, self.pages = Plane(), Pages()
            self.pages.current['content'] = render_remote_page(body, metadata)
            self.candidate()
            self.failure()
            self.assertEqual(self.pages.writes, 0)
            self.assertEqual(self.plane.index[KEY].status, 'needs_review')

    def test_recovery_requires_newer_ai_revision_and_matching_body(self):
        for ai, body in [(4, 'new'), (6, 'different')]:
            self.plane, self.pages = Plane(), Pages(body, entry(last_ai_revision_id=ai, source_revisions={'src': '2'}))
            self.pages.current['revision_id'] = 7
            self.candidate('new', revision='2')
            self.failure()
            self.assertEqual(self.pages.writes, 0)

    def test_index_failure_stops_remaining_candidates(self):
        self.candidate()
        path = self.run / 'wiki_staging' / '_manifest.json'
        manifest = json.loads(path.read_text())
        manifest['entries'].append({'key': 's/other/worldview', 'path': 'page.md'})
        path.write_text(json.dumps(manifest))
        self.plane.fail = True
        report = self.failure()
        self.assertEqual(len(report['pages']), 1)
        self.assertIn('s/other/worldview', str(report['errors']))
        self.assertTrue(report['pages'][0]['page_overwritten'])

    def test_first_create_failed_index_cannot_be_recovered(self):
        self.plane = Plane(False)
        self.plane.fail = True
        self.candidate()
        self.failure()
        self.plane.fail = False
        self.pages.orphan = True
        report = self.failure()
        self.assertEqual(report['published'], 0)
        self.assertEqual(self.plane.index, {})
        self.assertEqual(self.pages.writes, 1)

    def test_corrupt_index_and_lock_failure_persist_failed_report(self):
        self.candidate()
        for method in ('read_index', 'acquire_lock'):
            self.plane = Plane()
            def fail(*args): raise RuntimeError('blocked control plane')
            setattr(self.plane, method, fail)
            report = self.failure()
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(self.pages.writes, 0)

    def test_verify_has_no_overwrite_or_legacy_fields(self):
        self.candidate()
        report = SyncRunner(None, None, self.pages, self.plane).verify(self.run)
        self.assertFalse({'overwritten_human_edits', 'queued', 'conflicts'} & report.keys())
        self.assertEqual(self.pages.writes, 0)

if __name__ == '__main__': unittest.main()
