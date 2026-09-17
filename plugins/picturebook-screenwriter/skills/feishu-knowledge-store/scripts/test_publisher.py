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
from page_codec import render_remote_page
from publisher import NeedsReview, Publisher


def metadata():
    return {
        "key": "s/p/worldview",
        "page_type": "worldview",
        "source_node_tokens": ["source"],
        "source_revisions": {"source": "r1"},
        "last_ai_revision_id": 1,
    }


def page(body="# 正文"):
    return render_remote_page(body, metadata())


def entry(doc_token="doc-worldview"):
    return IndexEntry(
        key="s/p/worldview", doc_token=doc_token, wiki_node_token="node-worldview",
        source_revisions={"source": "r1"}, last_ai_revision_id=1,
        last_seen_revision_id=1, status="published",
    )


class FakeCli:
    def __init__(self):
        self.nodes = {"root": []}
        self.docs = {}
        self.created_titles = []
        self.updates = []
        self.update_error = None
        self.update_result = None

    def list_nodes(self, parent):
        if parent not in self.nodes:
            self.nodes[parent] = []
        return self.nodes[parent]

    def create_doc(self, parent, title, content=""):
        self.created_titles.append(title)
        if parent not in self.nodes:
            self.nodes[parent] = []
        token = f"doc-{len(self.created_titles)}"
        self.nodes[parent].append({"title": title, "node_token": f"node-{len(self.created_titles)}"})
        self.docs[token] = {"revision_id": 1, "content": content}
        return {"data": {"document": {"document_id": token, "revision_id": 1}}}

    def fetch_doc(self, token):
        return {"data": {"document": dict(self.docs[token])}}

    def update_doc(self, token, revision, content):
        if self.update_error:
            raise self.update_error
        result = self.update_result or {
            "code": 0,
            "data": {"document": {"revision_id": revision + 1}},
            "warnings": [],
        }
        if result.get("data", {}).get("result") == "partial_success" or result.get("warnings"):
            return result
        self.docs[token]["revision_id"] = revision + 1
        self.docs[token]["content"] = content
        return result


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.cli = FakeCli()
        self.publisher = Publisher(self.cli, "root")

    def test_initialize_creates_system_tree_only_once(self):
        tokens = self.publisher.initialize()
        self.assertEqual(self.cli.created_titles, [
            "00_使用说明", "AI知识库编辑说明", "01_知识内容",
            "02_导航与日志", "知识导航索引", "同步日志",
            "99_系统控制台", "同步索引", "同步锁", "冲突待处理",
        ])
        self.assertEqual(self.publisher.initialize(), tokens)
        self.assertEqual(len(self.cli.created_titles), 10)

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

    def test_revision_conflict_does_not_replace_human_page(self):
        current = {"revision_id": 1, "content": page("# 人工规则")}
        self.cli.docs[entry().doc_token] = dict(current)
        self.cli.update_error = RevisionConflict("changed")
        with self.assertRaises(RevisionConflict):
            self.publisher.conditional_update(entry(), current, {"source": "r2"})
        self.assertEqual(self.cli.docs["doc-worldview"]["content"], page("# 人工规则"))

    def test_partial_success_requires_review_and_does_not_mark_published(self):
        current = {"revision_id": 1, "content": page()}
        self.cli.docs[entry().doc_token] = dict(current)
        self.cli.update_result = {"code": 0, "data": {"result": "partial_success", "document": {"revision_id": 2}}, "warnings": ["partial"]}
        with self.assertRaises(NeedsReview):
            self.publisher.conditional_update(entry(), current, {"source": "r2"})

    def test_resource_bearing_page_requires_review(self):
        current = page("# 正文\n\n[资源](https://example.test/a)")
        self.cli.docs[entry().doc_token] = {"revision_id": 1, "content": current}
        current_document = {"revision_id": 1, "content": current}
        with self.assertRaises(NeedsReview):
            self.publisher.conditional_update(entry(), current_document, {"source": "r2"})


if __name__ == "__main__":
    unittest.main()
