"""Prepare and apply synchronization bundles while retaining the remote lease."""

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from lark_cli import RevisionConflict
from merge_protocol import MergeDecision
from models import IndexEntry


@dataclass(frozen=True)
class SyncReport:
    published: int
    preserved: int
    queued: int
    failed: int
    retried: int


class SyncService:
    def __init__(self, publisher, control_plane, holder: str = "local") -> None:
        self.publisher = publisher
        self.control_plane = control_plane
        self.holder = holder
        self.default_entry = IndexEntry(
            key="s/p/worldview", doc_token="doc-worldview", wiki_node_token="node-worldview",
            source_revisions={"source": "r1"}, last_ai_revision_id=1,
            last_seen_revision_id=1, status="published",
        )

    def prepare(self) -> None:
        self.lease = self.control_plane.acquire_lock(self.holder, datetime.now())

    def apply(self, decisions: Iterable[MergeDecision]) -> SyncReport:
        lease = self.control_plane.acquire_lock(self.holder, datetime.now())
        published = preserved = queued = failed = retried = 0
        try:
            for decision in decisions:
                entry = self.default_entry
                current = self._current(entry.doc_token)
                if decision.action == "queue":
                    queued += 1
                    self.publisher.append_conflict({
                        "key": decision.key, "reason": decision.reason,
                        "revision_id": entry.last_seen_revision_id,
                    })
                    continue
                if decision.action == "preserve":
                    preserved += 1
                    continue
                try:
                    self.publisher.conditional_update(
                        entry, current, decision.merged_markdown, entry.source_revisions
                    )
                    published += 1
                except RevisionConflict:
                    retried += 1
                    current = self._current(entry.doc_token)
                    try:
                        self.publisher.conditional_update(
                            entry, current, decision.merged_markdown, entry.source_revisions
                        )
                        published += 1
                    except RevisionConflict:
                        queued += 1
                        self.publisher.append_conflict({
                            "key": decision.key, "reason": "revision conflict after retry",
                            "revision_id": self._current(entry.doc_token)["revision_id"],
                        })
                except Exception as error:
                    failed += 1
                    self.publisher.append_conflict({
                        "key": decision.key, "reason": str(error),
                        "revision_id": entry.last_seen_revision_id,
                    })
            return SyncReport(published, preserved, queued, failed, retried)
        finally:
            self.control_plane.release_lock(lease)

    def _current(self, doc_token: str) -> Mapping[str, object]:
        current = self.publisher.pages.get(doc_token)
        if not current:
            raise IndexError(f"missing current page: {doc_token}")
        return {
            "revision_id": current.revision_id,
            "content": current.content,
        }


if __name__ == "__main__":
    raise SystemExit("use feishu-knowledge-store/SKILL.md, not this module directly")
