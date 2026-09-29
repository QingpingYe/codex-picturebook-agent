#!/usr/bin/env python3
"""Read-only source baseline projection tests."""

import json
from pathlib import Path
import tempfile
import unittest

from models import IndexEntry
from source_admission import AdmissionEntry, AdmissionPolicy, SourceAdmissionError
import source_baseline as sb


def entry(key="s/p/worldview", token="node-a", revision="r1", edit_time=1756572300000):
    return IndexEntry(
        key=key,
        doc_token=f"doc-{key}",
        wiki_node_token=f"node-{key}",
        source_revisions={token: revision},
        last_ai_revision_id=4,
        last_seen_revision_id=4,
        status="published",
        source_edit_times={token: edit_time},
    )


def admission_page(entries=None):
    return "# AI_KB_SOURCE_ADMISSION_V1\n```json\n" + json.dumps({
        "schema_version": 1,
        "entries": entries or [],
    }) + "\n```\n"


class FakeCli:
    def __init__(self, content=None, revision=7):
        self.content = content or admission_page()
        self.revision = revision
        self.fetched = []
        self.writes = []

    def fetch_doc(self, token):
        self.fetched.append(token)
        return {"data": {"document": {
            "revision_id": self.revision,
            "content": self.content,
        }}}

    def update_doc(self, *_args, **_kwargs):
        self.writes.append(("update", _args, _kwargs))
        raise AssertionError("read-only baseline must not write")

    def create_doc(self, *_args, **_kwargs):
        self.writes.append(("create", _args, _kwargs))
        raise AssertionError("read-only baseline must not write")


class SourceBaselineTests(unittest.TestCase):

    def test_projection_preserves_index_revision_and_entries(self):
        payload = sb.build_source_baseline(
            12,
            {"s/p/worldview": entry()},
            AdmissionPolicy(entries=(), revision_id=3, page_present=True),
        )
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["index_revision_id"], 12)
        self.assertEqual(payload["entries"], [{
            "key": "s/p/worldview",
            "source_revisions": {"node-a": "r1"},
            "source_edit_times": {"node-a": 1756572300000},
        }])
        self.assertEqual(payload["admission"]["revision_id"], 3)

    def test_missing_admission_projects_page_absent(self):
        payload = sb.build_source_baseline(12, {}, None)
        self.assertFalse(payload["admission"]["page_present"])
        self.assertEqual(payload["admission"]["entries"], [])

    def test_read_source_admission_reads_control_page(self):
        cli = FakeCli(admission_page([{
            "token": "tokA",
            "title": "标题",
            "decision": "exclude",
            "decided_by": "user",
            "decided_at": "2026-09-23T10:00:00+08:00",
            "reason": "排除",
        }]), revision=9)
        policy = sb.read_source_admission(cli, "admission-doc")
        self.assertTrue(policy.page_present)
        self.assertEqual(policy.revision_id, 9)
        self.assertEqual(policy.entries[0].token, "tokA")
        self.assertEqual(cli.writes, [])

    def test_read_source_admission_rejects_malformed_page(self):
        with self.assertRaises(SourceAdmissionError):
            sb.read_source_admission(FakeCli("# bad"), "admission-doc")

    def test_write_source_baseline_is_atomic_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source_baseline.json"
            sb.write_source_baseline(path, {"schema_version": 1})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                             {"schema_version": 1})
            self.assertFalse(Path(str(path) + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()