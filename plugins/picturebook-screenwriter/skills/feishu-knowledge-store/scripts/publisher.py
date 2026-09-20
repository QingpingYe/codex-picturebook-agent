"""Docx wiki publishing with revision preconditions and human-content safety."""

from dataclasses import dataclass, replace
from typing import Any, Mapping

from lark_cli import RevisionConflict
from models import IndexEntry
from page_codec import parse_remote_page, render_remote_page


class NeedsReview(RuntimeError):
    pass


CONTROL_TREE = (
    ("00_使用说明", ("AI知识库编辑说明",)),
    ("02_导航与日志", ("知识导航索引", "同步日志")),
    ("99_系统控制台", (
        "AI_KB_INDEX_V1",
        "AI_KB_LOCK_V1",
        "AI_KB_CONFLICT_QUEUE_V1",
    )),
)

TREE_ORDER = ("00_使用说明", "01_知识内容", "02_导航与日志", "99_系统控制台")


class Publisher:
    DEFAULT_REVISION_ADVANCE = 2
    MAX_METADATA_ATTEMPTS = 3

    def __init__(self, cli: Any, target_root: str, space_id: str) -> None:
        self.cli = cli
        self.target_root = target_root
        self.space_id = space_id
        self.tokens: dict[str, str] | None = None
        self.revision_advance = self.DEFAULT_REVISION_ADVANCE

    def initialize(self) -> dict[str, str]:
        if self.tokens is not None:
            return self.tokens
        tokens: dict[str, str] = {}
        for title in TREE_ORDER:
            parent, _ = self._find_or_create(self.target_root, title)
            tokens[title] = parent
            if title == "01_知识内容":
                tokens["content"] = parent
            children = dict(CONTROL_TREE).get(title, ())
            for child in children:
                child_token, _ = self._find_or_create(parent, child)
                canonical = {
                    "AI_KB_INDEX_V1": "index",
                    "AI_KB_LOCK_V1": "lock",
                    "AI_KB_CONFLICT_QUEUE_V1": "conflict",
                }
                if child in canonical:
                    tokens[canonical[child]] = child_token
        self.tokens = tokens
        return tokens

    def initialize_locked(self, control_plane, holder: str, now) -> dict[str, str]:
        lease = control_plane.acquire_lock(holder, now)
        try:
            return self.initialize()
        finally:
            control_plane.release_lock(lease)

    def fetch_current(self, doc_token: str) -> dict[str, Any]:
        document = self.cli.fetch_doc(doc_token).get("data", {}).get("document", {})
        revision = document.get("revision_id")
        content = document.get("content")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise NeedsReview(f"current page has invalid revision: {doc_token}")
        if not isinstance(content, str) or not content:
            raise NeedsReview(f"current page is empty or unreadable: {doc_token}")
        return {"revision_id": revision, "content": content}

    def publish_new(self, entry: IndexEntry, body: str, parent: str) -> IndexEntry:
        title = entry.key
        page = render_remote_page(body, self._metadata(entry, revision=0))
        result = self.cli.create_doc(parent, title, page)
        document = result.get("data", {}).get("document", {})
        revision = int(document.get("revision_id", 0))
        if revision <= 0:
            raise NeedsReview("created document did not return a positive revision")
        doc_token = document.get("document_id", document.get("doc_token", document.get("token")))
        if not doc_token:
            raise NeedsReview("created document did not return a usable doc token")
        node_token = self._node_token(parent, title)
        current = self.cli.fetch_doc(doc_token).get("data", {}).get("document", {})
        parsed = parse_remote_page(current["content"])
        if parsed.metadata["key"] != entry.key:
            raise NeedsReview("created page metadata key mismatch")
        fixed = self._write_page_with_metadata_fix(
            doc_token, int(current["revision_id"]), parsed.body, entry,
        )
        return replace(fixed, doc_token=doc_token, wiki_node_token=node_token)

    def conditional_update(
        self,
        entry: IndexEntry,
        current: Mapping[str, Any],
        merged_markdown: str,
        source_revisions: Mapping[str, str],
    ) -> IndexEntry:
        content = current.get("content", "")
        if not content:
            raise NeedsReview("current document is empty")
        parsed = parse_remote_page(content)
        if parsed.has_non_roundtrippable_content:
            raise NeedsReview("document contains resources or comments and must be reviewed")

        merged = parse_remote_page(merged_markdown)
        revision = int(current["revision_id"])
        final_revision = None
        for _attempt in range(self.MAX_METADATA_ATTEMPTS):
            predicted_revision = revision + self.revision_advance
            updated_metadata = dict(merged.metadata)
            updated_metadata["source_revisions"] = dict(source_revisions)
            updated_metadata["last_ai_revision_id"] = predicted_revision
            normalized_page = render_remote_page(merged.body, updated_metadata)
            result = self.cli.update_doc(entry.doc_token, revision, normalized_page)
            if result.get("warnings") or result.get("data", {}).get("result") == "partial_success":
                raise NeedsReview("partial or warned update")
            actual_revision = int(result["data"]["document"]["revision_id"])
            if actual_revision == predicted_revision:
                final_revision = actual_revision
                break
            self.revision_advance = actual_revision - revision
        if final_revision is None:
            raise NeedsReview("revision did not converge to metadata value after retries")
        return replace(
            entry,
            source_revisions=dict(source_revisions),
            last_ai_revision_id=final_revision,
            last_seen_revision_id=final_revision,
            status="published",
        )

    def _write_page_with_metadata_fix(
        self,
        doc_token: str,
        current_revision: int,
        body: str,
        entry: IndexEntry,
    ) -> IndexEntry:
        final_revision = None
        for _attempt in range(self.MAX_METADATA_ATTEMPTS):
            predicted_revision = current_revision + self.revision_advance
            updated_metadata = self._metadata(entry, predicted_revision)
            normalized = render_remote_page(body, updated_metadata)
            result = self.cli.update_doc(doc_token, current_revision, normalized)
            if result.get("warnings") or result.get("data", {}).get("result") == "partial_success":
                raise NeedsReview("partial or warned update")
            actual_revision = int(result["data"]["document"]["revision_id"])
            if actual_revision == predicted_revision:
                final_revision = actual_revision
                break
            self.revision_advance = actual_revision - current_revision
        if final_revision is None:
            raise NeedsReview("revision did not converge after initial metadata fix")
        return replace(
            entry,
            last_ai_revision_id=final_revision,
            last_seen_revision_id=final_revision,
            status="published",
        )

    def append_conflict(self, parent: str, record: Mapping[str, Any]) -> None:
        current = self.cli.fetch_doc(parent).get("data", {}).get("document", {})
        content = current.get("content", "# AI_KB_CONFLICT_QUEUE_V1\n")
        if not content.rstrip().endswith(f"[{record['key']}] {record['reason']}"):
            content = content.rstrip() + f"\n[{record['key']}] {record['reason']}\n"
        self.cli.update_doc(parent, int(current["revision_id"]), content)

    def _find_or_create(self, parent: str, title: str) -> tuple[str, bool]:
        matches = [node for node in self.cli.list_nodes(parent) if node.get("title") == title]
        if len(matches) > 1:
            raise NeedsReview(f"duplicate system page: {title}")
        existing = matches[0] if matches else None
        if existing:
            for key in ("node_token", "obj_token", "token"):
                if existing.get(key):
                    return existing[key], True
        response = self.cli.create_doc(parent, title, "")
        document = response.get("data", {}).get("document", {})
        token = document.get("document_id", document.get("doc_token", document.get("token")))
        if not token:
            raise NeedsReview("created document did not return a usable token")
        matches = [
            node
            for node in self.cli.list_nodes(self.space_id, parent_node_token=parent)
            if node.get("title") == title
        ]
        if len(matches) != 1:
            raise NeedsReview(f"created page has ambiguous node identity: {title}")
        for key in ("node_token", "obj_token", "token"):
            if matches[0].get(key):
                return matches[0][key], False
        return token, False

    @staticmethod
    def _metadata(entry: IndexEntry, revision: int | None = None) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "key": entry.key,
            "page_type": entry.key.split("/")[-1],
            "source_node_tokens": sorted(entry.source_revisions),
            "source_revisions": dict(entry.source_revisions),
            "last_ai_revision_id": revision if revision is not None else entry.last_ai_revision_id,
        }

    def _node_token(self, parent: str, title: str) -> str:
        matches = [
            node
            for node in self.cli.list_nodes(self.space_id, parent_node_token=parent)
            if node.get("title") == title
        ]
        if len(matches) != 1:
            raise NeedsReview(f"expected exactly one page named {title}")
        for key in ("node_token", "obj_token", "token"):
            if matches[0].get(key):
                return matches[0][key]
        raise NeedsReview(f"created page has no usable node token: {title}")
