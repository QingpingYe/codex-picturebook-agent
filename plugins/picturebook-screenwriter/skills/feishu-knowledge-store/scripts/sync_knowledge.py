"""Prepare and apply synchronization bundles while retaining the remote lease."""

from dataclasses import dataclass
from datetime import datetime, timezone
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
    def __init__(self, publisher, control_plane, holder: str = "local",
                 conflict_parent: str = "") -> None:
        self.publisher = publisher
        self.control_plane = control_plane
        self.holder = holder
        self.conflict_parent = conflict_parent

    def prepare(self) -> None:
        self.lease = self.control_plane.acquire_lock(self.holder, datetime.now())

    def apply(self, decisions: Iterable[MergeDecision],
              entries: Mapping[str, IndexEntry]) -> SyncReport:
        lease = self.control_plane.acquire_lock(self.holder, datetime.now(timezone.utc))
        published = preserved = queued = failed = retried = 0
        try:
            for decision in decisions:
                if decision.key not in entries:
                    raise KeyError(f"missing index entry for decision: {decision.key}")
                entry = entries[decision.key]
                current = self._current(entry.doc_token)
                if decision.action == "queue":
                    queued += 1
                    self.publisher.append_conflict(self.conflict_parent, {
                        "key": decision.key, "reason": decision.reason,
                        "revision_id": entry.last_seen_revision_id,
                    })
                    continue
                if decision.action == "preserve":
                    preserved += 1
                    continue
                try:
                    updated = self.publisher.conditional_update(
                        entry, current, decision.merged_markdown, entry.source_revisions
                    )
                    self.control_plane.update_index([updated])
                    published += 1
                except RevisionConflict:
                    retried += 1
                    current = self._current(entry.doc_token)
                    try:
                        updated = self.publisher.conditional_update(
                            entry, current, decision.merged_markdown, entry.source_revisions
                        )
                        self.control_plane.update_index([updated])
                        published += 1
                    except RevisionConflict:
                        queued += 1
                        self.publisher.append_conflict(self.conflict_parent, {
                            "key": decision.key, "reason": "revision conflict after retry",
                            "revision_id": self._current(entry.doc_token)["revision_id"],
                        })
                except Exception as error:
                    failed += 1
                    self.publisher.append_conflict(self.conflict_parent, {
                        "key": decision.key, "reason": str(error),
                        "revision_id": entry.last_seen_revision_id,
                    })
            return SyncReport(published, preserved, queued, failed, retried)
        finally:
            self.control_plane.release_lock(lease)

    def _current(self, doc_token: str) -> Mapping[str, object]:
        return self.publisher.fetch_current(doc_token)


if __name__ == "__main__":
    raise SystemExit("use feishu-knowledge-store/SKILL.md, not this module directly")
