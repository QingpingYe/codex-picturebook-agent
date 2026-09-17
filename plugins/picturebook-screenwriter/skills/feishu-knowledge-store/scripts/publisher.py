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
    ("99_系统控制台", ("同步索引", "同步锁", "冲突待处理")),
)

TREE_ORDER = ("00_使用说明", "01_知识内容", "02_导航与日志", "99_系统控制台")


class Publisher:
    def __init__(self, cli: Any, target_root: str) -> None:
        self.cli = cli
        self.target_root = target_root
        self.tokens: dict[str, str] | None = None

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
                tokens[child] = child_token
        self.tokens = tokens
        return tokens

    def initialize_locked(self, control_plane, holder: str, now) -> dict[str, str]:
        lease = control_plane.acquire_lock(holder, now)
        try:
            return self.initialize()
        finally:
            control_plane.release_lock(lease)

    def publish_new(self, entry: IndexEntry, body: str, parent: str) -> IndexEntry:
        initial_revision = 1
        page = render_remote_page(body, self._metadata(entry, initial_revision))
        result = self.cli.create_doc(parent, entry.doc_token, page)
        document = result.get("data", {}).get("document", {})
        revision = int(document.get("revision_id", 1))
        if revision != initial_revision:
            raise NeedsReview("created document did not start at revision 1")
        return replace(
            entry,
            last_ai_revision_id=revision,
            last_seen_revision_id=revision,
            status="published",
        )

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

        revision = int(current["revision_id"])
        predicted_revision = revision + 1
        merged = parse_remote_page(merged_markdown)
        updated_metadata = dict(merged.metadata)
        updated_metadata["source_revisions"] = dict(source_revisions)
        updated_metadata["last_ai_revision_id"] = predicted_revision
        normalized_page = render_remote_page(merged.body, updated_metadata)
        result = self.cli.update_doc(entry.doc_token, revision, normalized_page)
        if result.get("warnings") or result.get("data", {}).get("result") == "partial_success":
            raise NeedsReview("partial or warned update")

        updated_revision = int(result["data"]["document"]["revision_id"])
        if updated_revision != predicted_revision:
            raise NeedsReview("revision advanced differently than expected")
        return replace(
            entry,
            source_revisions=dict(source_revisions),
            last_ai_revision_id=updated_revision,
            last_seen_revision_id=updated_revision,
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
