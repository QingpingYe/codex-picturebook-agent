"""Shared schema rules for Feishu knowledge candidates and remote pages."""

from typing import Iterable


class SchemaError(ValueError):
    pass


COMMON_TYPES = frozenset({
    "ip-overview",
    "quality-rubric",
    "market-research",
})

DUAL_SCOPE_TYPES = frozenset({
    "creation-standards",
})

SYSTEM_TYPES = frozenset({
    "index",
    "log",
})

PROJECT_TYPES = frozenset({
    "worldview",
    "characters",
    "content-spec",
    "corrections",
    "creative-feature-ledger",
    "golden-sentence-registry",
    "prop-registry",
    "story-fingerprint-spec",
    "references",
})

PAGE_TYPES = COMMON_TYPES | DUAL_SCOPE_TYPES | SYSTEM_TYPES | PROJECT_TYPES


def normalize_series_id(page_type: str, series_id: str) -> str:
    _require_page_type(page_type)
    if page_type in SYSTEM_TYPES:
        return "system"
    if not series_id:
        raise SchemaError("Non-system page requires series_id")
    return series_id


def normalize_project_id(page_type: str, project_id: str) -> str:
    _require_page_type(page_type)
    if page_type in COMMON_TYPES:
        return "common"
    if page_type in SYSTEM_TYPES:
        return "system"
    if not project_id:
        raise SchemaError("Project-level page requires project_id")
    return project_id


def validate_revisions(tokens: Iterable[str], revisions: Iterable[str]) -> dict[str, str]:
    tokens = list(tokens)
    revisions = list(revisions)
    if not tokens:
        raise SchemaError("source_node_tokens cannot be empty")
    if len(tokens) != len(revisions):
        raise SchemaError("source_node_tokens and source_revision_parts must have equal lengths")
    if len(set(tokens)) != len(tokens):
        raise SchemaError("source_node_tokens must be unique")
    if any(not isinstance(token, str) or not token for token in tokens):
        raise SchemaError("source_node_tokens cannot contain blank values")
    if any(not isinstance(revision, str) or not revision for revision in revisions):
        raise SchemaError("source_revision_parts cannot contain blank values")
    return dict(zip(tokens, revisions))


def logical_key(series_id: str, project_id: str, page_type: str) -> str:
    normalized_series = normalize_series_id(page_type, series_id)
    normalized_project = normalize_project_id(page_type, project_id)
    return "/".join((normalized_series, normalized_project, page_type))


def _require_page_type(page_type: str) -> None:
    if page_type not in PAGE_TYPES:
        raise SchemaError(f"Unsupported page type: {page_type}")
