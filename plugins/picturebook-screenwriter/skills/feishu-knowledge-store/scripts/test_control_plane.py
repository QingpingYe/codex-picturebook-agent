import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from control_plane import ControlPlane, ControlPlaneCorrupt, LockHeld, LeaseOwnershipError
from lark_cli import RevisionConflict
from models import IndexEntry

FIXED_NOW = datetime(2026, 9, 17, 3, 0, tzinfo=timezone.utc)


def control_tokens():
    return {"index": "index-doc", "lock": "lock-doc"}


def empty_lock():
    return "# AI_KB_LOCK_V1\n```json\n{" + '"schema_version":1,"run_id":null,"holder":null,"started_at":null,"expires_at":null' + "}\n```\n"


def index_content(entries=None):
    return "# AI_KB_INDEX_V1\n```json\n" + json.dumps({"schema_version": 1, "entries": entries or []}) + "\n```\n"


class FakeCli:
    def __init__(self, lock_revision=1, lock_content=None, index_content=None):
        self.docs = {
            "lock-doc": {"revision_id": lock_revision, "content": lock_content or empty_lock()},
            "index-doc": {"revision_id": 2, "content": index_content or globals()["index_content"]()},
        }
        self.updates = []
        self.conflict_once = False
        self.conflict_content = None

    def fetch_doc(self, token):
        return {"data": {"document": dict(self.docs[token])}}

    def update_doc(self, token, revision, content):
        self.updates.append((token, revision, content))
        if self.conflict_once:
            self.conflict_once = False
            if self.conflict_content is not None:
                self.docs[token]["revision_id"] += 1
                self.docs[token]["content"] = self.conflict_content
            raise RevisionConflict("changed")
        if revision != self.docs[token]["revision_id"]:
            raise RevisionConflict("changed")
        self.docs[token]["revision_id"] += 1
        self.docs[token]["content"] = content
        return self.docs[token]["revision_id"]


class ControlPlaneTests(unittest.TestCase):
    def test_update_index_merges_entries_and_verifies_readback(self):
        cli = FakeCli()
        plane = ControlPlane(cli, control_tokens())
        updated = IndexEntry(
            key="s/p/worldview", doc_token="doc-world", wiki_node_token="node-world",
            source_revisions={"source": "r2"}, last_ai_revision_id=9,
            last_seen_revision_id=9, status="published",
        )
        result = plane.update_index([updated])
        self.assertEqual(result["s/p/worldview"], updated)
        self.assertEqual(cli.docs["index-doc"]["revision_id"], 3)

    def test_update_index_detects_postwrite_index_change(self):
        class RacyIndexCli(FakeCli):
            def update_doc(self, token, revision, content):
                result = super().update_doc(token, revision, content)
                self.docs[token]["content"] = index_content()
                return result

        plane = ControlPlane(RacyIndexCli(), control_tokens())
        with self.assertRaises(ControlPlaneCorrupt):
            plane.update_index([IndexEntry(
                key="s/p/worldview", doc_token="doc-world", wiki_node_token="node-world",
                source_revisions={"source": "r2"}, last_ai_revision_id=9,
                last_seen_revision_id=9, status="published",
            )])

    def test_first_writer_acquires_lock_at_revision(self):
        plane = ControlPlane(FakeCli(lock_revision=7, lock_content=empty_lock()), control_tokens())
        lease = plane.acquire_lock("alice@host", FIXED_NOW)
        self.assertEqual(lease.holder, "alice@host")
        with self.assertRaises(LockHeld):
            plane.acquire_lock("bob@host", FIXED_NOW)

    def test_acquire_lock_rechecks_written_lease(self):
        cli = FakeCli()
        plane = ControlPlane(cli, control_tokens())
        lease = plane.acquire_lock("alice@host", FIXED_NOW)
        self.assertEqual(lease.run_id, plane._read_lock()[1]["run_id"])

    def test_acquire_lock_detects_postwrite_race(self):
        class RacyCli(FakeCli):
            def update_doc(self, token, revision, content):
                result = super().update_doc(token, revision, content)
                self.docs[token]["content"] = "# AI_KB_LOCK_V1\n```json\n" + json.dumps({
                    "schema_version": 1,
                    "run_id": "rival",
                    "holder": "bob@host",
                    "started_at": "2026-09-17T03:00:00Z",
                    "expires_at": "2026-09-17T04:00:00Z",
                }) + "\n```\n"
                return result

        plane = ControlPlane(RacyCli(), control_tokens())
        with self.assertRaises(LockHeld):
            plane.acquire_lock("alice@host", FIXED_NOW)

    def test_acquire_lock_parses_update_result_revision(self):
        class JsonCli(FakeCli):
            def update_doc(self, token, revision, content):
                result = super().update_doc(token, revision, content)
                return {"data": {"document": {"revision_id": result}}}

        plane = ControlPlane(JsonCli(), control_tokens())
        lease = plane.acquire_lock("alice@host", FIXED_NOW)
        self.assertIsInstance(lease.revision_id, int)

    def test_release_lock_verifies_empty_state_after_write(self):
        class RacyReleaseCli(FakeCli):
            def update_doc(self, token, revision, content):
                result = super().update_doc(token, revision, content)
                payload = json.loads(content.split("```json\n", 1)[1].split("\n```", 1)[0])
                if payload.get("run_id") is None:
                    self.docs[token]["content"] = "# AI_KB_LOCK_V1\n```json\n" + json.dumps({
                        "schema_version": 1,
                        "run_id": "rival",
                        "holder": "bob@host",
                        "started_at": "2026-09-17T03:00:00Z",
                        "expires_at": "2026-09-17T04:00:00Z",
                    }) + "\n```\n"
                return result

        plane = ControlPlane(RacyReleaseCli(), control_tokens())
        lease = plane.acquire_lock("alice@host", FIXED_NOW)
        with self.assertRaises(LeaseOwnershipError):
            plane.release_lock(lease)

    def test_bad_index_stops_writes(self):
        plane = ControlPlane(FakeCli(index_content="# AI_KB_INDEX_V1\nnot-json"), control_tokens())
        with self.assertRaises(ControlPlaneCorrupt):
            plane.read_index()

    def test_index_rejects_invalid_entry_token(self):
        plane = ControlPlane(FakeCli(index_content=index_content([{
            "key": "s/p/worldview", "doc_token": "", "wiki_node_token": "node",
            "source_revisions": {"source": "1"}, "last_ai_revision_id": 1,
            "last_seen_revision_id": 1, "status": "published",
        }])), control_tokens())
        with self.assertRaisesRegex(ControlPlaneCorrupt, "doc_token"):
            plane.read_index()

    def test_conflicting_acquire_refetches_once_then_reports_holder(self):
        cli = FakeCli()
        cli.conflict_once = True
        cli.conflict_content = "# AI_KB_LOCK_V1\n```json\n" + json.dumps({
            "schema_version": 1, "run_id": "rival", "holder": "bob@host",
            "started_at": "2026-09-17T03:00:00Z", "expires_at": "2026-09-17T04:00:00Z",
        }) + "\n```\n"
        plane = ControlPlane(cli, control_tokens())
        with self.assertRaises(LockHeld):
            plane.acquire_lock("alice@host", FIXED_NOW)
        self.assertEqual(len(cli.updates), 1)

    def test_refresh_and_release_require_lease_identity(self):
        plane = ControlPlane(FakeCli(), control_tokens())
        lease = plane.acquire_lock("alice@host", FIXED_NOW)
        refreshed = plane.refresh_lock(lease, FIXED_NOW)
        self.assertEqual(refreshed.run_id, lease.run_id)
        with self.assertRaises(LeaseOwnershipError):
            plane.release_lock(type(lease)("other", lease.holder, lease.started_at, lease.expires_at, lease.revision_id))
        plane.release_lock(refreshed)

    def test_rebuild_rejects_duplicate_logical_keys(self):
        plane = ControlPlane(FakeCli(), control_tokens())
        pages = [
            {"doc_token": "doc-a", "wiki_node_token": "node-a", "revision_id": 2,
             "metadata": {"key": "s/p/worldview", "source_node_tokens": ["source"],
                          "source_revisions": {"source": "r1"}, "last_ai_revision_id": 1}},
            {"doc_token": "doc-b", "wiki_node_token": "node-b", "revision_id": 2,
             "metadata": {"key": "s/p/worldview", "source_node_tokens": ["source"],
                          "source_revisions": {"source": "r1"}, "last_ai_revision_id": 1}},
        ]
        with self.assertRaisesRegex(ControlPlaneCorrupt, "duplicate"):
            plane.rebuild_index(pages)

    def test_rebuild_restores_source_revisions_from_page_metadata(self):
        plane = ControlPlane(FakeCli(), control_tokens())
        pages = [{
            "doc_token": "doc-a", "wiki_node_token": "node-a", "revision_id": 2,
            "metadata": {"key": "s/p/worldview", "source_node_tokens": ["source-a"],
                         "source_revisions": {"source-a": "r7"}, "last_ai_revision_id": 5},
        }]
        index = plane.rebuild_index(pages)
        self.assertEqual(index["s/p/worldview"].source_revisions, {"source-a": "r7"})


if __name__ == "__main__":
    unittest.main()
