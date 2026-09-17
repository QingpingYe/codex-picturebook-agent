"""Deterministic human-priority merge classification and decision validation."""

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from page_codec import parse_remote_page, PageCodecError


@dataclass(frozen=True)
class MergeRequirement:
    required_action: Literal["publish", "preserve", "agent_decision"]
    reason: str


@dataclass(frozen=True)
class MergeDecision:
    key: str
    action: Literal["publish", "preserve", "queue"]
    merged_markdown: str | None
    reason: str


class MergeValidationError(ValueError):
    pass


def classify(base: str, current: str, candidate: str, source_changed: bool) -> MergeRequirement:
    human_changed = current != base
    if not source_changed:
        return MergeRequirement("preserve", "source_unchanged")
    if not human_changed:
        return MergeRequirement("publish", "source_only_change")
    if candidate == base:
        return MergeRequirement("preserve", "candidate_unchanged")
    return MergeRequirement("agent_decision", "human_and_source_changed")


def validate_decision(
    requirement: MergeRequirement,
    base: str,
    current: str,
    decision: MergeDecision,
) -> MergeDecision:
    if decision.action in ("queue", "preserve"):
        if not decision.reason:
            raise MergeValidationError("decision requires a non-empty reason")
        return decision
    if decision.action != "publish":
        raise MergeValidationError("unsupported decision action")
    if not decision.merged_markdown:
        raise MergeValidationError("publish decision requires merged markdown")

    try:
        parsed = parse_remote_page(decision.merged_markdown)
    except PageCodecError as error:
        raise MergeValidationError(f"merged page is invalid: {error}") from error

    base_lines = set(_page_lines(base))
    current_lines = set(_page_lines(current))
    matched_lines = set(_page_lines(parsed.body))

    human_added = current_lines - base_lines
    dropped_human = [line for line in human_added if line not in matched_lines]
    if dropped_human:
        first = sorted(dropped_human)[0]
        raise MergeValidationError(f"publish drops human content: 人工 {first}")

    human_deleted = base_lines - current_lines
    restored_human_deletion = [line for line in human_deleted if line in matched_lines]
    if restored_human_deletion:
        first = sorted(restored_human_deletion)[0]
        raise MergeValidationError(f"publish reintroduces human-deleted content: 人工删除 {first}")

    candidate_lines = set(_page_lines(decision.merged_markdown))
    missing_candidate_addition = current_lines - candidate_lines
    if missing_candidate_addition:
        first = sorted(missing_candidate_addition)[0]
        raise MergeValidationError(f"publish misses source or human content: {first}")

    return decision


def _page_lines(markdown: str) -> list[str]:
    try:
        return parse_remote_page(markdown).body.splitlines()
    except PageCodecError:
        return markdown.splitlines()
