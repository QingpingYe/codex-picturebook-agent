"""Docx wiki publishing with revision preconditions and human-content safety."""

import re
from dataclasses import dataclass, replace
from typing import Any, Mapping

from control_plane import (
    render_empty_conflict_queue,
    render_empty_index,
    render_empty_lock,
)
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

    def __init__(self, cli: Any, target_root: str, space_id: str,
                 root_mode: str = "node") -> None:
        self.cli = cli
        self.target_root = target_root
        self.space_id = space_id
        if root_mode not in {"space", "node"}:
            raise ValueError("root_mode must be 'space' or 'node'")
        self.root_mode = root_mode
        self.tokens: dict[str, str] | None = None
        self.revision_advance = self.DEFAULT_REVISION_ADVANCE

    def initialize(self) -> dict[str, str]:
        if self.tokens is not None:
            return self.tokens
        tokens: dict[str, str] = {}
        for title in TREE_ORDER:
            parent, _ = self._find_or_create_root(title)
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

    def resolve_control_plane(self) -> dict[str, str]:
        tokens: dict[str, str] = {}
        for title in TREE_ORDER:
            token = self._existing_root_token(title)
            if token is None:
                raise NeedsReview(f"missing system container: {title}")
            tokens[title] = token
        for parent_key, children in (
            ("00_使用说明", ("AI知识库编辑说明",)),
            ("02_导航与日志", ("知识导航索引", "同步日志")),
            ("99_系统控制台", (
                "AI_KB_INDEX_V1",
                "AI_KB_LOCK_V1",
                "AI_KB_CONFLICT_QUEUE_V1",
            )),
        ):
            for child in children:
                token = self._existing_token(tokens[parent_key], child)
                if token is None:
                    raise NeedsReview(f"missing system page: {child}")
                tokens[child] = token
        tokens["index"] = tokens["AI_KB_INDEX_V1"]
        tokens["lock"] = tokens["AI_KB_LOCK_V1"]
        tokens["conflict"] = tokens["AI_KB_CONFLICT_QUEUE_V1"]
        tokens["content"] = tokens["01_知识内容"]
        return tokens

    def _find_or_create_root(self, title: str) -> tuple[str, bool]:
        if self.root_mode == "space":
            existing = self._existing_root_token(title)
            if existing:
                return existing, True
            seed_content = {
                "AI_KB_INDEX_V1": render_empty_index(),
                "AI_KB_LOCK_V1": render_empty_lock(),
                "AI_KB_CONFLICT_QUEUE_V1": render_empty_conflict_queue(),
            }
            response = self.cli.create_space_doc(
                self.space_id, title, seed_content.get(title, ""),
            )
            document = response.get("data", {}).get("document", {})
            token = document.get(
                "document_id", document.get("doc_token", document.get("token")),
            )
            if not token:
                raise NeedsReview("created document did not return a usable token")
            matches = [
                node
                for node in self.cli.list_nodes(self.space_id)
                if node.get("title") == title
            ]
            if len(matches) != 1:
                raise NeedsReview(f"created page has ambiguous node identity: {title}")
            for key in ("node_token", "obj_token", "token"):
                if matches[0].get(key):
                    return matches[0][key], False
            return token, False
        return self._find_or_create(self.target_root, title)

    def _existing_root_token(self, title: str) -> str | None:
        if self.root_mode == "space":
            return self._existing_token(self.space_id, title, space_root=True)
        return self._existing_token(self.target_root, title)

    def fetch_current(self, doc_token: str) -> dict[str, Any]:
        document = self.cli.fetch_doc(doc_token).get("data", {}).get("document", {})
        revision = document.get("revision_id")
        content = document.get("content")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise NeedsReview(f"current page has invalid revision: {doc_token}")
        if not isinstance(content, str) or not content:
            raise NeedsReview(f"current page is empty or unreadable: {doc_token}")
        return {"revision_id": revision, "content": content}

    def fetch_revision(self, doc_token: str, revision_id: int) -> dict[str, Any]:
        if isinstance(revision_id, bool) or not isinstance(revision_id, int) or revision_id <= 0:
            raise NeedsReview(f"historical page has invalid revision: {doc_token}")
        document = self.cli.fetch_doc_revision(doc_token, revision_id).get("data", {}).get("document", {})
        revision = document.get("revision_id")
        content = document.get("content")
        if revision != revision_id or not isinstance(content, str) or not content:
            raise NeedsReview(f"historical page is unreadable: {doc_token}@{revision_id}")
        return {"revision_id": revision, "content": content}

    def publish_new(self, entry: IndexEntry, body: str, parent: str) -> IndexEntry:
        title = entry.key
        existing_nodes = [
            node
            for node in self.cli.list_nodes(self.space_id, parent_node_token=parent)
            if node.get("title") == title
        ]
        if existing_nodes:
            raise NeedsReview(f"logical key page already exists: {title}")
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
        verified = self.fetch_current(entry.doc_token)
        verified_page = parse_remote_page(verified["content"])
        if verified_page.body != merged.body:
            raise NeedsReview("updated page body did not match merged content")
        if verified_page.metadata["last_ai_revision_id"] != final_revision:
            raise NeedsReview("updated page metadata revision did not match write result")
        if verified_page.metadata["source_revisions"] != dict(source_revisions):
            raise NeedsReview("updated page source revisions did not match merge decision")
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
        current_document = self.cli.fetch_doc(parent).get("data", {}).get("document", {})
        current = {
            "revision_id": current_document.get("revision_id"),
            "content": current_document.get("content", "# AI_KB_CONFLICT_QUEUE_V1\n"),
        }
        before = self._conflict_records(current["content"])
        content = current.get("content", "# AI_KB_CONFLICT_QUEUE_V1\n")
        if not content.rstrip().endswith(f"[{record['key']}] {record['reason']}"):
            content = content.rstrip() + f"\n[{record['key']}] {record['reason']}\n"
        result = self.cli.update_doc(parent, int(current["revision_id"]), content)
        if (
            not isinstance(result, Mapping)
            or result.get("warnings")
            or result.get("data", {}).get("result") == "partial_success"
        ):
            raise NeedsReview("partial, warned, or invalid conflict queue update")
        update_revision = result.get("data", {}).get("document", {}).get("revision_id")
        if isinstance(update_revision, bool) or not isinstance(update_revision, int) or update_revision < 0:
            raise NeedsReview("conflict queue update did not return a valid revision")
        verified_document = self.cli.fetch_doc(parent).get("data", {}).get("document", {})
        verified_revision = verified_document.get("revision_id")
        if verified_revision != update_revision:
            raise NeedsReview("conflict queue readback revision did not match update result")
        after = self._conflict_records(verified_document.get("content", ""))
        if after[:len(before)] != before or not after or after[-1] != (record["key"], record["reason"]):
            raise NeedsReview("conflict queue readback did not preserve records")

    def _find_or_create(self, parent: str, title: str) -> tuple[str, bool]:
        existing = self._existing_token(parent, title)
        if existing:
            return existing, True
        seed_content = {
            "AI_KB_INDEX_V1": render_empty_index(),
            "AI_KB_LOCK_V1": render_empty_lock(),
            "AI_KB_CONFLICT_QUEUE_V1": render_empty_conflict_queue(),
        }
        response = self.cli.create_doc(parent, title, seed_content.get(title, ""))
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

    def _existing_token(self, parent: str, title: str,
                        space_root: bool = False) -> str | None:
        matches = [
            node
            for node in self.cli.list_nodes(
                self.space_id,
                parent_node_token=None if space_root else parent,
            )
            if node.get("title") == title
        ]
        if len(matches) > 1:
            raise NeedsReview(f"duplicate system page: {title}")
        if not matches:
            return None
        for key in ("node_token", "obj_token", "token"):
            if matches[0].get(key):
                return matches[0][key]
        return None

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

    @staticmethod
    def _conflict_records(content: str) -> list[tuple[str, str]]:
        records: list[tuple[str, str]] = []
        for line in content.splitlines():
            match = re.fullmatch(r"\[([^\]]+)\] (.+)", line.strip())
            if match:
                records.append((match.group(1), match.group(2)))
        return records
