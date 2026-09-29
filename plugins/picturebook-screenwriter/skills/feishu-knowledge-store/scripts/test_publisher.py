import sys
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lark_cli import RevisionConflict
from models import IndexEntry
from page_codec import parse_candidate, parse_remote_page, render_remote_page
from publisher import NeedsReview, Publisher


def metadata():
    return {
        "key": "s/p/worldview",
        "page_type": "worldview",
        "source_node_tokens": ["source"],
        "source_revisions": {"source": "r1"},
        "last_ai_revision_id": 1,
    }


def page(body="# 正文", revision=1):
    return render_remote_page(body, {**metadata(), "last_ai_revision_id": revision})


def entry(doc_token="doc-worldview"):
    return IndexEntry(
        key="s/p/worldview", doc_token=doc_token, wiki_node_token="node-worldview",
        source_revisions={"source": "r1"}, last_ai_revision_id=1,
        last_seen_revision_id=1, status="published",
    )


class FakeCli:
    def __init__(self, create_revision=3):
        self.create_revision = create_revision
        self.nodes = {"root": []}
        self.docs = {}
        self.created_titles = []
        self.updates = []
        self.update_error = None
        self.update_result = None

    def list_nodes(self, space_id, parent_node_token=None, page_limit=10):
        if parent_node_token not in self.nodes:
            self.nodes[parent_node_token] = []
        return self.nodes[parent_node_token]

    def create_doc(self, parent, title, content=""):
        self.created_titles.append(title)
        if parent not in self.nodes:
            self.nodes[parent] = []
        token = f"doc-{len(self.created_titles)}"
        node_token = f"node-{len(self.created_titles)}"
        self.nodes[parent].append({"title": title, "node_token": node_token})
        # lark-cli 1.0.96 creates docx revisions starting at 3.
        document = {"revision_id": self.create_revision, "content": content}
        self.docs[token] = document
        self.docs[node_token] = document
        return {"data": {"document": {"document_id": token, "revision_id": self.create_revision}}}

    def fetch_doc(self, token):
        return {"data": {"document": dict(self.docs[token])}}

    def update_doc(self, token, revision, content):
        if self.update_error:
            raise self.update_error
        result = self.update_result or {
            "code": 0,
            "data": {"document": {"revision_id": revision + 2}},
            "warnings": [],
        }
        if result.get("data", {}).get("result") == "partial_success" or result.get("warnings"):
            return result
        self.docs[token]["revision_id"] = revision + 2
        self.docs[token]["content"] = content
        return result


class SpaceAwareCli(FakeCli):
    def __init__(self):
        super().__init__()
        self.list_node_calls = []
        self.nodes["content-root"] = []

    def list_nodes(self, space_id, parent_node_token=None, page_limit=10):
        self.list_node_calls.append((space_id, parent_node_token, page_limit))
        return self.nodes.get(parent_node_token, [])


class SpaceInitializeCli(SpaceAwareCli):
    def __init__(self):
        super().__init__()
        self.space_created = []
        self.nodes[None] = []

    def create_space_doc(self, space_id, title, content=""):
        self.space_created.append(title)
        number = len(self.space_created)
        token = f"space-doc-{number}"
        node_token = f"space-node-{number}"
        self.nodes[None].append({"title": title, "node_token": node_token})
        self.docs[token] = {"revision_id": 3, "content": content}
        return {"data": {"document": {"document_id": token, "revision_id": 3}}}


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.cli = FakeCli()
        self.publisher = Publisher(self.cli, "root", "target-space")

    def test_initialize_creates_system_tree_only_once(self):
        tokens = self.publisher.initialize()
        self.assertEqual(self.cli.created_titles, [
            "00_使用说明", "AI知识库编辑说明", "01_知识内容",
            "02_导航与日志", "知识导航索引", "同步日志",
            "99_系统控制台", "AI_KB_INDEX_V1", "AI_KB_LOCK_V1",
        ])
        self.assertEqual(self.publisher.initialize(), tokens)
        self.assertEqual(len(self.cli.created_titles), 9)

    def test_initialize_uses_actual_control_page_titles(self):
        tokens = self.publisher.initialize()
        self.assertEqual(self.cli.created_titles, [
            "00_使用说明", "AI知识库编辑说明", "01_知识内容",
            "02_导航与日志", "知识导航索引", "同步日志",
            "99_系统控制台", "AI_KB_INDEX_V1", "AI_KB_LOCK_V1",
        ])
        self.assertEqual(tokens["index"], "node-8")
        self.assertEqual(tokens["lock"], "node-9")
        self.assertNotIn("conflict", tokens)

    def test_initialize_rejects_duplicate_system_page(self):
        self.cli.nodes["root"] = [
            {"title": "00_使用说明", "node_token": "node-a"},
            {"title": "00_使用说明", "node_token": "node-b"},
        ]
        with self.assertRaises(NeedsReview):
            self.publisher.initialize()

    def test_initialize_locked_acquires_and_releases_the_lease(self):
        class Plane:
            def __init__(self):
                self.acquired = 0
                self.released = 0

            def acquire_lock(self, holder, now):
                self.acquired += 1
                return object()

            def release_lock(self, lease):
                self.released += 1

        plane = Plane()
        self.publisher.initialize_locked(plane, "alice", datetime.now(timezone.utc))
        self.assertEqual(plane.acquired, 1)
        self.assertEqual(plane.released, 1)

    def test_resolve_control_plane_fails_without_creating_missing_pages(self):
        self.cli.nodes["root"] = [
            {"title": "00_使用说明", "node_token": "node-00"},
            {"title": "01_知识内容", "node_token": "node-01"},
            {"title": "02_导航与日志", "node_token": "node-02"},
            {"title": "99_系统控制台", "node_token": "node-99"},
        ]
        with self.assertRaises(NeedsReview):
            self.publisher.resolve_control_plane()
        self.assertEqual(self.cli.created_titles, [])

    def test_resolve_control_plane_supports_space_root(self):
        cli = SpaceAwareCli()
        cli.nodes[None] = [
            {"title": "00_使用说明", "node_token": "node-00"},
            {"title": "01_知识内容", "node_token": "node-01"},
            {"title": "02_导航与日志", "node_token": "node-02"},
            {"title": "99_系统控制台", "node_token": "node-99"},
        ]
        cli.nodes["node-00"] = [{"title": "AI知识库编辑说明", "node_token": "node-guide"}]
        cli.nodes["node-02"] = [
            {"title": "知识导航索引", "node_token": "node-nav"},
            {"title": "同步日志", "node_token": "node-log"},
        ]
        cli.nodes["node-99"] = [
            {"title": "AI_KB_INDEX_V1", "node_token": "node-index"},
            {"title": "AI_KB_LOCK_V1", "node_token": "node-lock"},
            {"title": "AI_KB_SOURCE_ADMISSION_V1", "node_token": "node-admission"},
            {"title": "AI_KB_CONFLICT_QUEUE_V1", "node_token": "node-conflict"},
        ]
        publisher = Publisher(cli, "ignored", "target-space", root_mode="space")
        tokens = publisher.resolve_control_plane()
        self.assertEqual(tokens["00_使用说明"], "node-00")
        self.assertEqual(tokens["content"], "node-01")
        self.assertEqual(tokens["99_系统控制台"], "node-99")
        self.assertEqual(tokens["admission"], "node-admission")
        self.assertNotIn("conflict", tokens)
        self.assertTrue(any(call[1] is None for call in cli.list_node_calls))

    def test_resolve_control_plane_keeps_admission_optional(self):
        cli = SpaceAwareCli()
        cli.nodes[None] = [
            {"title": "00_使用说明", "node_token": "node-00"},
            {"title": "01_知识内容", "node_token": "node-01"},
            {"title": "02_导航与日志", "node_token": "node-02"},
            {"title": "99_系统控制台", "node_token": "node-99"},
        ]
        cli.nodes["node-00"] = [{"title": "AI知识库编辑说明", "node_token": "node-guide"}]
        cli.nodes["node-02"] = [
            {"title": "知识导航索引", "node_token": "node-nav"},
            {"title": "同步日志", "node_token": "node-log"},
        ]
        cli.nodes["node-99"] = [
            {"title": "AI_KB_INDEX_V1", "node_token": "node-index"},
            {"title": "AI_KB_LOCK_V1", "node_token": "node-lock"},
        ]
        publisher = Publisher(cli, "ignored", "target-space", root_mode="space")
        self.assertNotIn("admission", publisher.resolve_control_plane())
        with self.assertRaises(NeedsReview):
            publisher.resolve_control_plane(require_admission=True)

    def test_initialize_creates_space_root_system_tree(self):
        cli = SpaceInitializeCli()
        publisher = Publisher(cli, None, "target-space", root_mode="space")
        tokens = publisher.initialize()
        self.assertEqual(cli.space_created, [
            "00_使用说明", "01_知识内容", "02_导航与日志", "99_系统控制台",
        ])
        self.assertEqual(cli.created_titles, [
            "AI知识库编辑说明", "知识导航索引", "同步日志",
            "AI_KB_INDEX_V1", "AI_KB_LOCK_V1",
        ])
        self.assertIsNotNone(tokens["index"])

    def test_initialize_seeds_valid_control_documents(self):
        tokens = self.publisher.initialize()
        self.assertEqual(
            self.cli.docs[tokens["index"]]["content"],
            '# AI_KB_INDEX_V1\n```json\n{"entries":[],"schema_version":1}\n```\n',
        )
        self.assertIn('"expires_at":null', self.cli.docs[tokens["lock"]]["content"])
        self.assertNotIn("conflict", tokens)

    def test_resolve_control_plane_does_not_require_conflict_page(self):
        self.publisher.initialize()
        tokens = self.publisher.resolve_control_plane()
        self.assertEqual(set(tokens).intersection({"index", "lock", "content"}), {"index", "lock", "content"})
        self.assertNotIn("conflict", tokens)

    def test_publish_new_preserves_source_times(self):
        result = self.publisher.publish_new(replace(entry(), source_edit_times={"source": 123}), "# 正文", "content-root")
        self.assertEqual(result.source_edit_times, {"source": 123})
        self.assertEqual(parse_remote_page(self.cli.docs[result.doc_token]["content"]).metadata["source_edit_times"], {"source": 123})

    def test_conditional_update_uses_readback_revision_and_source_times(self):
        current = {"revision_id": 1, "content": page()}
        self.cli.docs[entry().doc_token] = dict(current)
        original_update = self.cli.update_doc
        def update_with_later_readback(token, revision, content):
            result = original_update(token, revision, content)
            self.cli.docs[token]["revision_id"] += 1
            return result
        self.cli.update_doc = update_with_later_readback
        result = self.publisher.conditional_update(entry(), current, page("# 新正文"), {"source": "r2"}, {"source": 123})
        self.assertEqual((result.last_ai_revision_id, result.last_seen_revision_id), (3, 4))
        self.assertEqual(result.source_edit_times, {"source": 123})

    def test_publish_readback_accepts_presentation_only_markdown_changes(self):
        original_update = self.cli.update_doc
        def normalized_update(token, revision, content):
            result = original_update(token, revision, content)
            parsed = parse_remote_page(self.cli.docs[token]["content"])
            self.cli.docs[token]["content"] = render_remote_page(parsed.body.replace("**New**", "New"), parsed.metadata)
            return result
        self.cli.update_doc = normalized_update
        created = self.publisher.publish_new(entry(), "# **New**", "content-root")
        self.assertEqual(created.last_seen_revision_id, 5)
        current = {"revision_id": 1, "content": page()}
        self.cli.docs[entry().doc_token] = dict(current)
        updated = self.publisher.conditional_update(entry(), current, page("# **New**"), {"source": "r2"})
        self.assertEqual(updated.last_seen_revision_id, 3)

    def test_readback_revision_cannot_precede_verified_ai_revision(self):
        current = {"revision_id": 1, "content": page()}
        self.cli.docs[entry().doc_token] = dict(current)
        def misleading_update(token, revision, content):
            self.cli.docs[token] = {"revision_id": 2, "content": content}
            return {"code": 0, "data": {"document": {"revision_id": 3}}, "warnings": []}
        self.cli.update_doc = misleading_update
        with self.assertRaises(NeedsReview):
            self.publisher.conditional_update(entry(), current, page("# New"), {"source": "r2"})

    def test_changed_source_vector_without_times_discards_old_times(self):
        old = replace(entry(), source_edit_times={"source": 100})
        current = {"revision_id": 1, "content": page()}
        self.cli.docs[old.doc_token] = dict(current)
        result = self.publisher.conditional_update(old, current, page("# New"), {"source": "r2"})
        self.assertIsNone(result.source_edit_times)
        self.assertNotIn("source_edit_times", parse_remote_page(self.cli.docs[old.doc_token]["content"]).metadata)

    def test_unchanged_source_vector_without_times_preserves_old_times(self):
        old = replace(entry(), source_edit_times={"source": 100})
        current = {"revision_id": 1, "content": page()}
        self.cli.docs[old.doc_token] = dict(current)
        result = self.publisher.conditional_update(old, current, page("# New"), {"source": "r1"})
        self.assertEqual(result.source_edit_times, {"source": 100})
        self.assertEqual(parse_remote_page(self.cli.docs[old.doc_token]["content"]).metadata["source_edit_times"], {"source": 100})

    def test_unchanged_vector_preserves_times_through_candidate_pipeline(self):
        old = replace(entry(), source_edit_times={"source": 100})
        current = {"revision_id": 1, "content": page()}
        self.cli.docs[old.doc_token] = dict(current)
        candidate = parse_candidate("""---
series_id: s
project_id: p
page_type: worldview
source_node_tokens: [source]
source_revision_parts: [r1]
---
# New
""")
        merged = render_remote_page(candidate.body, candidate.metadata)
        result = self.publisher.conditional_update(old, current, merged, candidate.metadata["source_revisions"])
        self.assertEqual(result.source_edit_times, {"source": 100})
        self.assertEqual(parse_remote_page(self.cli.docs[old.doc_token]["content"]).metadata["source_edit_times"], {"source": 100})

    def test_partial_update_is_read_back_before_review_without_retry(self):
        current = {"revision_id": 1, "content": page()}
        self.cli.docs[entry().doc_token] = dict(current)
        writes, reads = [], []
        original_fetch = self.cli.fetch_doc
        def partial_write(token, revision, content):
            writes.append(revision)
            self.cli.docs[token] = {"revision_id": 2, "content": content}
            return {"code": 0, "data": {"result": "partial_success", "document": {"revision_id": 2}}, "warnings": ["partial"]}
        def tracked_fetch(token):
            reads.append(token)
            return original_fetch(token)
        self.cli.update_doc = partial_write
        self.cli.fetch_doc = tracked_fetch
        with self.assertRaises(NeedsReview):
            self.publisher.conditional_update(entry(), current, page("# New"), {"source": "r2"})
        self.assertEqual(writes, [1])
        self.assertEqual(reads, [entry().doc_token])

    def test_revision_conflict_refetches_before_review(self):
        current = {"revision_id": 1, "content": page()}
        self.cli.docs[entry().doc_token] = dict(current)
        self.cli.update_error = RevisionConflict("changed")
        fetched = []
        original_fetch = self.cli.fetch_doc
        def tracked_fetch(token):
            fetched.append(token)
            return original_fetch(token)
        self.cli.fetch_doc = tracked_fetch
        with self.assertRaises(NeedsReview):
            self.publisher.conditional_update(entry(), current, page("# 新正文"), {"source": "r2"})
        self.assertEqual(fetched, [entry().doc_token])
        self.assertEqual(len(self.cli.updates), 0)

    def test_revision_conflict_does_not_replace_human_page(self):
        current = {"revision_id": 1, "content": page("# 人工规则")}
        self.cli.docs[entry().doc_token] = dict(current)
        self.cli.update_error = RevisionConflict("changed")
        with self.assertRaises(NeedsReview):
            self.publisher.conditional_update(entry(), current, page("# 新规则"), {"source": "r2"})
        self.assertEqual(self.cli.docs["doc-worldview"]["content"], page("# 人工规则"))

    def test_partial_success_requires_review_and_does_not_mark_published(self):
        current = {"revision_id": 1, "content": page()}
        self.cli.docs[entry().doc_token] = dict(current)
        self.cli.update_result = {"code": 0, "data": {"result": "partial_success", "document": {"revision_id": 2}}, "warnings": ["partial"]}
        with self.assertRaises(NeedsReview):
            self.publisher.conditional_update(entry(), current, page("# 新规则"), {"source": "r2"})

    def test_conditional_update_writes_merged_page_not_current_page(self):
        current = {"revision_id": 1, "content": page("# 人工规则")}
        self.cli.docs[entry().doc_token] = dict(current)
        self.publisher.conditional_update(entry(), current, page("# 人工规则\n\n新资料"), {"source": "r2"})
        expected = render_remote_page(
            "# 人工规则\n\n新资料",
            {**metadata(), "source_revisions": {"source": "r2"}, "last_ai_revision_id": 3},
        )
        self.assertEqual(self.cli.docs[entry().doc_token]["content"], expected)

    def test_conditional_update_rejects_metadata_drift_after_write(self):
        current = {"revision_id": 1, "content": page("# 人工规则")}
        self.cli.docs[entry().doc_token] = dict(current)
        original_update = self.cli.update_doc

        def drift_update(token, revision, content):
            result = original_update(token, revision, content)
            self.cli.docs[token]["content"] = render_remote_page(
                "# 人工规则\n\n新资料", {**metadata(), "last_ai_revision_id": 99}
            )
            return result

        self.cli.update_doc = drift_update
        with self.assertRaises(NeedsReview):
            self.publisher.conditional_update(
                entry(), current, page("# 人工规则\n\n新资料"), {"source": "r2"}
            )

    def test_publish_new_accepts_create_revision_and_records_corrected_value(self):
        zero_entry = replace(entry(), last_ai_revision_id=0, last_seen_revision_id=0)
        result = self.publisher.publish_new(zero_entry, "# 正文", "content-root")
        self.assertEqual(result.last_ai_revision_id, 5)
        self.assertEqual(result.last_seen_revision_id, 5)
        parsed = parse_remote_page(self.cli.docs["doc-1"]["content"])
        self.assertEqual(parsed.metadata["last_ai_revision_id"], 5)

    def test_publish_new_uses_logical_key_title_and_returns_real_tokens(self):
        self.cli = FakeCli()
        self.publisher = Publisher(self.cli, "root", "target-space")
        result = self.publisher.publish_new(entry(), "# 正文", "content-root")
        self.assertEqual(self.cli.created_titles, ["s/p/worldview"])
        self.assertEqual(result.doc_token, "doc-1")
        self.assertEqual(result.wiki_node_token, "node-1")
        self.assertEqual(result.last_ai_revision_id, 5)

    def test_publish_new_rejects_existing_logical_key_page(self):
        self.cli = FakeCli()
        self.cli.nodes["content-root"] = [{"title": entry().key, "node_token": "node-old"}]
        self.publisher = Publisher(self.cli, "root", "target-space")
        with self.assertRaises(NeedsReview):
            self.publisher.publish_new(entry(), "# 正文", "content-root")
        self.assertEqual(self.cli.created_titles, [])

    def test_publish_new_rejects_duplicate_created_by_rival(self):
        class DuplicateCreatingCli(FakeCli):
            def create_doc(self, parent, title, content=""):
                result = super().create_doc(parent, title, content)
                self.nodes[parent].append({"title": title, "node_token": "node-rival"})
                return result

        self.cli = DuplicateCreatingCli()
        self.publisher = Publisher(self.cli, "root", "target-space")
        with self.assertRaises(NeedsReview):
            self.publisher.publish_new(entry(), "# 正文", "content-root")

    def test_fetch_current_returns_validated_revision_and_content(self):
        current = page("# 正文")
        self.cli.docs[entry().doc_token] = {"revision_id": 9, "content": current}
        self.assertEqual(
            self.publisher.fetch_current(entry().doc_token),
            {"revision_id": 9, "content": current},
        )

    def test_fetch_revision_returns_requested_content(self):
        self.cli.fetch_doc_revision = lambda token, revision: {
            "data": {"document": {"revision_id": revision, "content": page("# 历史版本")}}
        }
        result = self.publisher.fetch_revision("doc-worldview", 4)
        self.assertEqual(result, {"revision_id": 4, "content": page("# 历史版本")})

    def test_fetch_revision_rejects_invalid_revision(self):
        with self.assertRaises(NeedsReview):
            self.publisher.fetch_revision("doc-worldview", 0)

    def test_node_lookup_uses_space_id_and_parent_token(self):
        cli = SpaceAwareCli()
        publisher = Publisher(cli, "content-root", "target-space")
        publisher.publish_new(entry(), "# 正文", "content-root")
        self.assertEqual(cli.list_node_calls[-1], ("target-space", "content-root", 10))

    def test_publish_new_recovers_a_create_with_an_invalid_revision(self):
        self.cli = FakeCli(create_revision=0)
        self.publisher = Publisher(self.cli, "root", "target-space")
        zero_entry = replace(entry(), last_ai_revision_id=0, last_seen_revision_id=0)
        result = self.publisher.publish_new(zero_entry, "# 正文", "content-root")
        self.assertEqual(result.key, zero_entry.key)
        self.assertEqual(result.status, "published")

    def test_publish_new_recovers_a_create_without_a_token(self):
        original_create = self.cli.create_doc

        def tokenless_create(parent, title, content=""):
            original_create(parent, title, content)
            return {"code": 0, "data": {"document": {"revision_id": 3}}}

        self.cli.create_doc = tokenless_create
        result = self.publisher.publish_new(entry(), "# 正文", "content-root")
        self.assertEqual(result.key, entry().key)

    def test_publish_new_rejects_an_invalid_response_without_a_page(self):
        def no_page(parent, title, content=""):
            return {"code": 0, "data": {"document": {"revision_id": 0}}}

        self.cli.create_doc = no_page
        with self.assertRaises(NeedsReview) as caught:
            self.publisher.publish_new(entry(), "# 正文", "content-root")
        self.assertFalse(caught.exception.page_written)

    def test_publish_new_rejects_partial_metadata_correction(self):
        zero_entry = replace(entry(), last_ai_revision_id=0, last_seen_revision_id=0)
        self.cli.update_result = {
            "code": 0,
            "data": {"result": "partial_success", "document": {"revision_id": 5}},
            "warnings": ["partial"],
        }
        with self.assertRaises(NeedsReview):
            self.publisher.publish_new(zero_entry, "# 正文", "content-root")

    def test_publish_new_recovers_warned_creation_by_readback(self):
        original_create = self.cli.create_doc
        def warned_create(parent, title, content=""):
            result = original_create(parent, title, content)
            result["warnings"] = ["partial create"]
            return result
        self.cli.create_doc = warned_create
        result = self.publisher.publish_new(entry(), "# 正文", "content-root")
        self.assertEqual(result.key, entry().key)
        self.assertTrue(result.doc_token)
        self.assertEqual(result.status, "published")

    def test_publish_new_rejects_warned_creation_without_a_page(self):
        def warned_without_create(parent, title, content=""):
            return {"code": 0, "data": {"document": {"revision_id": 3}}, "warnings": ["no write"]}
        self.cli.create_doc = warned_without_create
        with self.assertRaises(NeedsReview):
            self.publisher.publish_new(entry(), "# 正文", "content-root")
        self.assertEqual(self.cli.created_titles, [])

    def test_publish_new_requires_review_when_creation_raises(self):
        def uncertain_create(parent, title, content=""):
            raise TimeoutError("request timed out")
        self.cli.create_doc = uncertain_create
        with self.assertRaises(NeedsReview):
            self.publisher.publish_new(entry(), "# 正文", "content-root")

    def test_confirmed_write_survives_a_failed_metadata_correction(self):
        current = {"revision_id": 1, "content": page()}
        self.cli.docs[entry().doc_token] = dict(current)
        self.publisher.revision_advance = 1
        original_update = self.cli.update_doc
        calls = []

        def correction_conflicts(token, revision, content):
            calls.append(revision)
            if len(calls) == 1:
                return original_update(token, revision, content)
            raise RevisionConflict("changed")

        self.cli.update_doc = correction_conflicts
        with self.assertRaises(NeedsReview) as caught:
            self.publisher.conditional_update(entry(), current, page("# 新正文"), {"source": "r1"})
        self.assertTrue(caught.exception.page_written)
        self.assertEqual(caught.exception.after_revision, 3)

    def test_confirmed_write_survives_a_failing_diagnostic_read(self):
        current = {"revision_id": 1, "content": page()}
        self.cli.docs[entry().doc_token] = dict(current)
        self.publisher.revision_advance = 1
        original_update = self.cli.update_doc
        calls = []

        def correction_conflicts(token, revision, content):
            calls.append(revision)
            if len(calls) == 1:
                return original_update(token, revision, content)
            raise RevisionConflict("changed")

        self.cli.update_doc = correction_conflicts
        original_fetch = self.publisher.fetch_current
        fetches = []

        def flaky_fetch(token):
            fetches.append(token)
            if len(fetches) > 1:
                raise TimeoutError("readback timed out")
            return original_fetch(token)

        self.publisher.fetch_current = flaky_fetch
        with self.assertRaises(NeedsReview) as caught:
            self.publisher.conditional_update(entry(), current, page("# 新正文"), {"source": "r1"})
        self.assertTrue(caught.exception.page_written)
        self.assertEqual(caught.exception.after_revision, 3)

    def test_uncertain_update_with_an_unreadable_page_stays_unknown(self):
        current = {"revision_id": 1, "content": page()}
        self.cli.docs[entry().doc_token] = dict(current)

        def timeout(token, revision, content):
            raise TimeoutError("request timed out")

        def unreadable(token):
            raise TimeoutError("readback timed out")

        self.cli.update_doc = timeout
        self.publisher.fetch_current = unreadable
        with self.assertRaises(NeedsReview) as caught:
            self.publisher.conditional_update(entry(), current, page("# 新正文"), {"source": "r1"})
        self.assertFalse(caught.exception.page_written)
        self.assertIsNone(caught.exception.after_revision)

    def test_publish_new_recovers_a_create_that_raised_after_writing(self):
        original_create = self.cli.create_doc

        def create_then_fail(parent, title, content=""):
            original_create(parent, title, content)
            raise TimeoutError("request timed out")

        self.cli.create_doc = create_then_fail
        result = self.publisher.publish_new(entry(), "# 正文", "content-root")
        self.assertEqual(result.key, entry().key)
        self.assertEqual(result.status, "published")

    def test_publish_new_rejects_a_logical_key_page_deeper_in_the_tree(self):
        self.cli = FakeCli()
        self.cli.nodes["content-root"] = [{"title": "container", "node_token": "node-container"}]
        self.cli.nodes["node-container"] = [{"title": entry().key, "node_token": "node-deep"}]
        self.publisher = Publisher(self.cli, "root", "target-space")
        with self.assertRaises(NeedsReview):
            self.publisher.publish_new(entry(), "# 正文", "content-root")
        self.assertEqual(self.cli.created_titles, [])

    def test_conditional_update_requires_review_for_error_code(self):
        current = {"revision_id": 1, "content": page()}
        self.cli.docs[entry().doc_token] = dict(current)
        self.cli.update_result = {"code": 1, "data": {"document": {"revision_id": 3}}, "warnings": []}
        with self.assertRaises(NeedsReview):
            self.publisher.conditional_update(entry(), current, page("# 新正文"), {"source": "r2"})

    def test_conditional_update_records_predicted_revision(self):
        current = {"revision_id": 1, "content": page("# 人工规则")}
        self.cli.docs[entry().doc_token] = dict(current)
        result = self.publisher.conditional_update(
            entry(), current, page("# 人工规则\n\n新资料"), {"source": "r2"}
        )
        self.assertEqual(result.last_ai_revision_id, 3)
        self.assertEqual(result.last_seen_revision_id, 3)
        parsed = parse_remote_page(self.cli.docs[entry().doc_token]["content"])
        self.assertEqual(parsed.metadata["last_ai_revision_id"], 3)

    def test_conditional_update_converges_on_unexpected_revision(self):
        current = {"revision_id": 1, "content": page("# 人工规则")}
        self.cli.docs[entry().doc_token] = dict(current)
        self.publisher.revision_advance = 1
        result = self.publisher.conditional_update(
            entry(), current, page("# 人工规则\n\n新资料"), {"source": "r2"}
        )
        self.assertEqual(result.last_ai_revision_id, 5)
        self.assertEqual(self.publisher.revision_advance, 2)
        parsed = parse_remote_page(self.cli.docs[entry().doc_token]["content"])
        self.assertEqual(parsed.metadata["last_ai_revision_id"], 5)

    def test_resource_bearing_page_requires_review(self):
        current = page("# 正文\n\n[资源](https://example.test/a)")
        self.cli.docs[entry().doc_token] = {"revision_id": 1, "content": current}
        current_document = {"revision_id": 1, "content": current}
        with self.assertRaises(NeedsReview):
            self.publisher.conditional_update(entry(), current_document, page("# 新规则"), {"source": "r2"})



if __name__ == "__main__":
    unittest.main()
