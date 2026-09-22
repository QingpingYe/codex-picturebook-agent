"""Pure classification of a candidate against the authoritative remote index."""

from dataclasses import dataclass
from typing import Literal, Mapping

from models import IndexEntry


@dataclass(frozen=True)
class TargetAction:
    action: Literal["first_publish", "update", "preserve", "queue"]
    reason: str


def classify_target(
    candidate: IndexEntry,
    remote_index: Mapping[str, IndexEntry],
) -> TargetAction:
    indexed = remote_index.get(candidate.key)
    if indexed is None:
        return TargetAction("first_publish", "index_entry_absent")
    if indexed.status == "needs_review":
        return TargetAction("queue", "index_entry_needs_review")
    if indexed.status == "archived":
        return TargetAction("queue", "index_entry_archived")
    if dict(candidate.source_revisions) == dict(indexed.source_revisions):
        return TargetAction("preserve", "source_unchanged")
    return TargetAction("update", "source_changed")
