"""Docx wiki publishing with revision preconditions and human-content safety."""

from dataclasses import dataclass, replace
from typing import Any, Iterable, Iterator, Mapping

from control_plane import (
    render_empty_index,
    render_empty_lock,
)
from lark_cli import RevisionConflict
from models import IndexEntry
from page_codec import parse_remote_page, render_remote_page
from remote_markdown import mark_insensitive


class NeedsReview(RuntimeError):
    """Review required; ``page_written`` marks an already confirmed page write."""

    def __init__(self, message: str, page_written: bool = False,
                 after_revision: int | None = None) -> None:
        super().__init__(message)
        self.page_written = page_written
        self.after_revision = after_revision


CONTROL_TREE = (
    ("00_使用说明", ("AI知识库编辑说明",)),
    ("02_导航与日志", ("知识导航索引", "同步日志")),
    ("99_系统控制台", (
        "AI_KB_INDEX_V1",
        "AI_KB_LOCK_V1",
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

    def resolve_control_plane(self, require_admission: bool = False) -> dict[str, str]:
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
                "AI_KB_SOURCE_ADMISSION_V1",
            )),
        ):
            for child in children:
                token = self._existing_token(tokens[parent_key], child)
                if token is None:
                    if child == "AI_KB_SOURCE_ADMISSION_V1" and not require_admission:
                        continue
                    raise NeedsReview(f"missing system page: {child}")
                tokens[child] = token
        tokens["index"] = tokens["AI_KB_INDEX_V1"]
        tokens["lock"] = tokens["AI_KB_LOCK_V1"]
        if "AI_KB_SOURCE_ADMISSION_V1" in tokens:
            tokens["admission"] = tokens["AI_KB_SOURCE_ADMISSION_V1"]
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
        # The logical key must be unique across the whole target tree, not only
        # among the direct children of the content container.
        if any(node.get("title") == title for node in self._iter_tree([parent])):
            raise NeedsReview(f"logical key page already exists: {title}")
        page = render_remote_page(body, self._metadata(entry, revision=0))
        try:
            result = self.cli.create_doc(parent, title, page)
        except Exception as error:
            return self._recover_uncertain_create(parent, title, entry, body, error)
        if (not isinstance(result, Mapping) or result.get("code", 0) != 0
                or result.get("warnings") or result.get("data", {}).get("result") == "partial_success"):
            return self._recover_uncertain_create(parent, title, entry, body, None)
        document = result.get("data", {}).get("document", {})
        revision = document.get("revision_id")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
            return self._recover_uncertain_create(parent, title, entry, body, None)
        doc_token = document.get("document_id", document.get("doc_token", document.get("token")))
        if not doc_token:
            return self._recover_uncertain_create(parent, title, entry, body, None)
        node_token = self._node_token(parent, title)
        current = self.fetch_current(doc_token)
        parsed = parse_remote_page(current["content"])
        if (mark_insensitive(parsed.body) != mark_insensitive(parse_remote_page(page).body)
                or parsed.metadata != self._metadata(entry, revision=0)):
            raise NeedsReview("created page did not match body and metadata")
        fixed = self._write_page_with_metadata_fix(
            doc_token, current["revision_id"], parsed.body, entry,
        )
        return replace(fixed, doc_token=doc_token, wiki_node_token=node_token)

    def conditional_update(
        self,
        entry: IndexEntry,
        current: Mapping[str, Any],
        merged_markdown: str,
        source_revisions: Mapping[str, str],
        source_edit_times: Mapping[str, int] | None = None,
    ) -> IndexEntry:
        content = current.get("content", "")
        if not content:
            raise NeedsReview("current document is empty")
        parsed = parse_remote_page(content)
        if parsed.has_non_roundtrippable_content:
            raise NeedsReview("document contains resources or comments and must be reviewed")

        merged = parse_remote_page(merged_markdown)
        times = source_edit_times if source_edit_times is not None else merged.metadata.get("source_edit_times")
        if (times is None and source_edit_times is None and merged.metadata.get("source_edit_times") is None
                and dict(source_revisions) == entry.source_revisions):
            times = entry.source_edit_times
        updated_entry = replace(entry, source_revisions=dict(source_revisions),
                                source_edit_times=None if times is None else dict(times))
        return self._write_page_with_metadata_fix(entry.doc_token, int(current["revision_id"]), merged.body, updated_entry)

    def _write_page_with_metadata_fix(
        self,
        doc_token: str,
        current_revision: int,
        body: str,
        entry: IndexEntry,
    ) -> IndexEntry:
        revision = current_revision
        confirmed_revision: int | None = None

        def review(message: str, observed: int | None = None) -> NeedsReview:
            # A correction attempt that follows a verified write must not erase the
            # fact that the candidate body is already on the page.
            seen = confirmed_revision if observed is None else observed
            return NeedsReview(message, page_written=seen is not None, after_revision=seen)

        for _attempt in range(self.MAX_METADATA_ATTEMPTS):
            predicted_revision = revision + self.revision_advance
            updated_metadata = self._metadata(entry, predicted_revision)
            normalized = render_remote_page(body, updated_metadata)
            try:
                result = self.cli.update_doc(doc_token, revision, normalized)
            except RevisionConflict as error:
                observed = self._observe_page_write(doc_token, body, confirmed_revision)
                raise review("revision changed during update", observed) from error
            except Exception as error:
                observed = self._observe_page_write(doc_token, body, confirmed_revision)
                raise review("update outcome is uncertain", observed) from error
            if (not isinstance(result, Mapping) or result.get("code", 0) != 0
                    or result.get("warnings") or result.get("data", {}).get("result") == "partial_success"):
                observed = self._observe_page_write(doc_token, body, confirmed_revision)
                raise review("partial or warned update", observed)
            actual_revision = result.get("data", {}).get("document", {}).get("revision_id")
            if isinstance(actual_revision, bool) or not isinstance(actual_revision, int) or actual_revision <= revision:
                observed = self._observe_page_write(doc_token, body, confirmed_revision)
                raise review("update returned an invalid revision", observed)
            verified = self.fetch_current(doc_token)
            verified_page = parse_remote_page(verified["content"])
            if mark_insensitive(verified_page.body) != mark_insensitive(body) or verified_page.metadata != updated_metadata:
                raise review("updated page readback did not match body and metadata")
            if verified["revision_id"] < max(predicted_revision, actual_revision):
                raise review("readback revision precedes the verified update")
            if actual_revision == predicted_revision:
                return replace(entry, last_ai_revision_id=predicted_revision,
                               last_seen_revision_id=verified["revision_id"], status="published")
            if verified["revision_id"] != actual_revision:
                return replace(entry, last_ai_revision_id=predicted_revision,
                               last_seen_revision_id=verified["revision_id"], status="published")
            # The candidate body and metadata are confirmed on the page; only the
            # AI revision prediction was off, so retry the metadata correction.
            confirmed_revision = verified["revision_id"]
            self.revision_advance = actual_revision - revision
            revision = verified["revision_id"]
        raise review("revision did not converge after metadata correction")

    def _observe_page_write(self, doc_token: str, body: str,
                            confirmed: int | None) -> int | None:
        """Best-effort readback used to classify an ambiguous update.

        Returns the observed revision when the candidate body is already on the
        page, or the previously confirmed revision. A read failure yields None
        instead of replacing the review that is already being raised.
        """
        if confirmed is not None:
            return confirmed
        try:
            current = self.fetch_current(doc_token)
            parsed = parse_remote_page(current["content"])
        except Exception:
            return None
        if mark_insensitive(parsed.body) == mark_insensitive(body):
            return current["revision_id"]
        return None

    def _recover_uncertain_create(self, parent: str, title: str, entry: IndexEntry,
                                  body: str, cause: Exception | None) -> IndexEntry:
        """Read back a create whose outcome is unknown before giving up on it."""
        node = self._existing_node(parent, title)
        if node is None:
            if cause is not None:
                raise NeedsReview("create outcome is uncertain") from cause
            raise NeedsReview("create outcome is uncertain")
        doc_token = next((node[key] for key in ("obj_token", "node_token", "token")
                          if node.get(key)), None)
        if doc_token is None:
            raise NeedsReview("created page has no usable token", page_written=True)
        try:
            current = self.fetch_current(doc_token)
        except Exception as read_error:
            raise NeedsReview("created page could not be read back",
                              page_written=True) from read_error
        parsed = parse_remote_page(current["content"])
        if (parsed.metadata.get("key") != entry.key
                or mark_insensitive(parsed.body) != mark_insensitive(body)):
            raise NeedsReview("created page did not match body and metadata",
                              page_written=True)
        fixed = self._write_page_with_metadata_fix(
            doc_token, current["revision_id"], parsed.body, entry,
        )
        return replace(fixed, doc_token=doc_token,
                       wiki_node_token=node.get("node_token") or doc_token)

    def _existing_node(self, parent: str, title: str) -> Mapping[str, Any] | None:
        matches = [
            node
            for node in self.cli.list_nodes(self.space_id, parent_node_token=parent)
            if node.get("title") == title
        ]
        if len(matches) > 1:
            raise NeedsReview(f"duplicate system page: {title}")
        return matches[0] if matches else None

    def _iter_tree(self, extra_roots: Iterable[str] = ()) -> Iterator[Mapping[str, Any]]:
        """Yield every node under the target root, breadth first.

        ``extra_roots`` additionally walk subtrees that may not be reachable from
        the configured root, such as the content container passed to a publisher
        whose root mode points somewhere else.
        """
        root_parent = None if self.root_mode == "space" else self.target_root
        pending = list(self.cli.list_nodes(self.space_id, parent_node_token=root_parent))
        seen: set[str] = set()
        for extra in extra_roots:
            if extra and extra not in seen:
                seen.add(extra)
                pending.extend(self.cli.list_nodes(self.space_id, parent_node_token=extra))
        while pending:
            node = pending.pop(0)
            yield node
            token = next((node[key] for key in ("node_token", "obj_token", "token")
                          if node.get(key)), None)
            if not token or token in seen:
                continue
            seen.add(token)
            pending.extend(self.cli.list_nodes(self.space_id, parent_node_token=token))

    def _find_or_create(self, parent: str, title: str) -> tuple[str, bool]:
        existing = self._existing_token(parent, title)
        if existing:
            return existing, True
        seed_content = {
            "AI_KB_INDEX_V1": render_empty_index(),
            "AI_KB_LOCK_V1": render_empty_lock(),
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
        metadata = {
            "schema_version": 1,
            "key": entry.key,
            "page_type": entry.key.split("/")[-1],
            "source_node_tokens": sorted(entry.source_revisions),
            "source_revisions": dict(entry.source_revisions),
            "last_ai_revision_id": revision if revision is not None else entry.last_ai_revision_id,
        }
        if entry.source_edit_times is not None:
            metadata["source_edit_times"] = dict(entry.source_edit_times)
        return metadata

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
