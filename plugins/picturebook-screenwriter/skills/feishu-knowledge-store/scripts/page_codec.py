"""Strict reader/system envelopes for authoritative knowledge pages."""

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from remote_markdown import normalize_remote_markdown
from shared_schema import PAGE_TYPES
from shared_schema import logical_key as shared_logical_key
from shared_schema import normalize_project_id, normalize_series_id, validate_revisions


SYSTEM_HEADING = "## 系统元数据（请勿编辑）"
_RESOURCE_OR_COMMENT = re.compile(
    r"(?is)(<!--|<\s*(?:comment|resource|attachment)\b|\[\s*(?:资源|附件|resource|attachment)\s*\])"
)


class PageCodecError(ValueError):
    pass


@dataclass(frozen=True)
class Candidate:
    body: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RemotePage:
    body: str
    metadata: dict[str, Any]
    has_non_roundtrippable_content: bool


def logical_key(page: Candidate | Mapping[str, Any]) -> str:
    if isinstance(page, Candidate):
        return page.metadata["key"]
    metadata = page
    page_type = metadata.get("page_type")
    series_id = normalize_series_id(page_type, metadata.get("series_id", ""))
    project_id = normalize_project_id(page_type, metadata.get("project_id", ""))
    return shared_logical_key(series_id, project_id, page_type)


def parse_candidate(markdown: str) -> Candidate:
    if not isinstance(markdown, str) or not markdown.startswith("---\n"):
        raise PageCodecError("candidate must begin with YAML frontmatter")
    end = markdown.find("\n---\n", 4)
    if end < 0:
        raise PageCodecError("candidate YAML frontmatter is not closed")
    frontmatter = _parse_simple_yaml(markdown[4:end])
    body = markdown[end + 5:]
    key = logical_key(frontmatter)
    if "key" in frontmatter and frontmatter["key"] != key:
        raise PageCodecError("changed keys are not allowed")
    source_node_tokens = frontmatter.get("source_node_tokens")
    source_revision_parts = frontmatter.get("source_revision_parts")
    if not isinstance(source_node_tokens, list) or not isinstance(source_revision_parts, list):
        raise PageCodecError("source_node_tokens and source_revision_parts must be lists")
    try:
        source_revisions = validate_revisions(source_node_tokens, source_revision_parts)
    except ValueError as error:
        raise PageCodecError(str(error)) from error
    metadata = {
        "schema_version": 1,
        "key": key,
        "page_type": frontmatter["page_type"],
        "source_node_tokens": source_node_tokens,
        "source_revisions": source_revisions,
        "last_ai_revision_id": frontmatter.get("last_ai_revision_id", 0),
    }
    _validate_metadata(metadata)
    return Candidate(body, metadata)


def render_remote_page(body: str, metadata: Mapping[str, Any]) -> str:
    if not isinstance(body, str):
        raise PageCodecError("page body must be text")
    with_schema = dict(metadata)
    with_schema.setdefault("schema_version", 1)
    normalized = _validate_metadata(with_schema)
    reader_body = body[:-1] if body.endswith("\n") else body
    return f"{reader_body}\n\n{SYSTEM_HEADING}\n```json\n{_canonical_json(normalized)}\n```\n"


def parse_remote_page(markdown: str) -> RemotePage:
    if not isinstance(markdown, str):
        raise PageCodecError("remote page must be text")
    markdown = normalize_remote_markdown(markdown, kind="page")
    count = markdown.count(SYSTEM_HEADING)
    if count != 1:
        raise PageCodecError("system metadata must occur exactly once")
    prefix, envelope = markdown.split(SYSTEM_HEADING, 1)
    if not prefix.endswith("\n\n"):
        raise PageCodecError("system metadata must be a separate final section")
    match = re.fullmatch(r"\n```json\n(\{.*\})\n```\n*", envelope, flags=re.DOTALL)
    if match is None:
        raise PageCodecError("system metadata must be the final section")
    try:
        metadata = json.loads(match.group(1))
    except json.JSONDecodeError as error:
        raise PageCodecError("system metadata is not valid JSON") from error
    if not isinstance(metadata, dict):
        raise PageCodecError("system metadata must be an object")
    normalized = _validate_metadata(metadata)
    body = prefix[:-1]  # keep the reader body's original terminal newline
    return RemotePage(body, normalized, bool(_RESOURCE_OR_COMMENT.search(body)))


def _validate_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    required = {"schema_version", "key", "page_type", "source_node_tokens", "source_revisions", "last_ai_revision_id"}
    if set(metadata) != required:
        raise PageCodecError("metadata must contain exactly the required fields")
    if metadata["schema_version"] != 1:
        raise PageCodecError("unsupported metadata schema_version")
    page_type = metadata["page_type"]
    if not isinstance(page_type, str) or page_type not in PAGE_TYPES:
        raise PageCodecError("invalid page_type")
    key = metadata["key"]
    if not isinstance(key, str) or key.count("/") != 2 or key.split("/")[-1] != page_type:
        raise PageCodecError("invalid key")
    if any(not segment.strip() for segment in key.split("/")):
        raise PageCodecError("invalid key")
    tokens = metadata["source_node_tokens"]
    if not isinstance(tokens, list) or not tokens or any(not isinstance(token, str) or not token.strip() for token in tokens):
        raise PageCodecError("invalid source_node_tokens")
    if len(set(tokens)) != len(tokens):
        raise PageCodecError("invalid source_node_tokens")
    source_revisions = metadata["source_revisions"]
    if not isinstance(source_revisions, dict) or not source_revisions:
        raise PageCodecError("invalid source_revisions")
    if any(not isinstance(token, str) or not token.strip() or not isinstance(revision, str) or not revision.strip()
           for token, revision in source_revisions.items()):
        raise PageCodecError("invalid source_revisions")
    if set(source_revisions) != set(tokens):
        raise PageCodecError("source_revisions must match source_node_tokens")
    revision = metadata["last_ai_revision_id"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise PageCodecError("invalid last_ai_revision_id")
    return {name: metadata[name] for name in sorted(metadata)}


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the deliberately small scalar/list frontmatter subset used by staging."""
    result: dict[str, Any] = {}
    active_list: str | None = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - ") and active_list:
            result[active_list].append(_yaml_scalar(line[4:]))
            continue
        if line.startswith((" ", "\t")) or ":" not in line:
            raise PageCodecError("unsupported candidate YAML")
        key, raw = line.split(":", 1)
        key, raw = key.strip(), raw.strip()
        if not key or key in result:
            raise PageCodecError("invalid candidate YAML")
        if raw:
            result[key] = _yaml_scalar(raw)
            active_list = None
        else:
            result[key] = []
            active_list = key
    return result


def _yaml_scalar(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        return [_yaml_scalar(item.strip()) for item in value[1:-1].split(",") if item.strip()]
    if value in ("true", "false"):
        return value == "true"
    if re.fullmatch(r"-?[0-9]+", value):
        return int(value)
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value
