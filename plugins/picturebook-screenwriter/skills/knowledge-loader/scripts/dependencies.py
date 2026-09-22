"""Creation dependency locking for authoritative Feishu evidence."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Mapping, TYPE_CHECKING


if TYPE_CHECKING:
    from load_knowledge import KnowledgeEvidenceBundle


ARTIFACT_TYPES = frozenset({
    "positioning", "topic_plan", "worldview", "characters", "outline", "script",
})


@dataclass(frozen=True)
class DependencyRecord:
    artifact_id: str
    artifact_type: str
    built_at: str
    evidence: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class StaleReason:
    key: str
    reason: str
    locked_revision_id: int | None
    current_revision_id: int | None
    status: str | None


def find_stale_dependencies(
    record: DependencyRecord,
    current_index: Mapping[str, Any],
    current_bundle: Mapping[str, Any] | KnowledgeEvidenceBundle | None = None,
) -> tuple[StaleReason, ...]:
    stale: list[StaleReason] = []
    fetched_items = (
        {item["key"]: item for item in _bundle_items(current_bundle)}
        if current_bundle is not None else None
    )
    current_is_offline = (
        bool(_field(current_bundle, "offline", False))
        if current_bundle is not None else False
    )

    for item in record.evidence:
        key = item["key"]
        current = current_index.get(key)
        if current is None:
            stale.append(StaleReason(key, "missing", item["revision_id"], None, None))
            continue

        index_revision = _entry_revision(current)
        status = _field(current, "status")

        if fetched_items is None:
            if index_revision != item["revision_id"]:
                stale.append(StaleReason(
                    key, "revision_changed", item["revision_id"], index_revision, status,
                ))
            elif status == "needs_review":
                stale.append(StaleReason(
                    key, "needs_review", item["revision_id"], index_revision, status,
                ))
            elif status == "archived":
                stale.append(StaleReason(
                    key, "archived", item["revision_id"], index_revision, status,
                ))
            else:
                stale.append(StaleReason(
                    key, "index_unverified", item["revision_id"], index_revision, status,
                ))
            continue

        fetched = fetched_items.get(key)
        if fetched is None:
            stale.append(StaleReason(key, "missing", item["revision_id"], None, status))
            continue

        current_revision = fetched["revision_id"]
        current_index_synced = bool(_field(fetched, "index_synced", True))
        if current_revision != item["revision_id"]:
            stale.append(StaleReason(
                key, "revision_changed", item["revision_id"], current_revision, status,
            ))
        elif current_is_offline:
            stale.append(StaleReason(
                key, "current_offline", item["revision_id"], current_revision, status,
            ))
        elif not current_index_synced:
            stale.append(StaleReason(
                key, "index_unsynced", item["revision_id"], current_revision, status,
            ))
        elif status == "needs_review":
            stale.append(StaleReason(
                key, "needs_review", item["revision_id"], current_revision, status,
            ))
        elif status == "archived":
            stale.append(StaleReason(
                key, "archived", item["revision_id"], current_revision, status,
            ))
    return tuple(stale)


def build_dependency_record(bundle: Mapping[str, Any] | KnowledgeEvidenceBundle,
                            artifact_id: str, artifact_type: str) -> DependencyRecord:
    if _bundle_offline(bundle):
        raise ValueError("离线缓存不能生成权威依赖记录")
    if (not isinstance(artifact_id, str) or not artifact_id.strip()
            or artifact_type not in ARTIFACT_TYPES):
        raise ValueError("invalid artifact id or type")
    items = tuple(_bundle_items(bundle))
    if not items:
        raise ValueError("knowledge bundle has no evidence")
    items = _validate_evidence(items)
    return DependencyRecord(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        built_at=_field(bundle, "fetched_at"),
        evidence=items,
    )


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    try:
        return getattr(value, name, default)
    except AttributeError:
        return default


def _entry_revision(entry: Any) -> int | None:
    revision = _field(entry, "revision_id")
    if revision is None:
        revision = _field(entry, "last_seen_revision_id")
    return revision if isinstance(revision, int) and not isinstance(revision, bool) else None


def _bundle_offline(bundle: Any) -> bool:
    return bool(_field(bundle, "offline", False))


def _bundle_items(bundle: Any) -> tuple[dict[str, Any], ...]:
    raw_items = _field(bundle, "items", ())
    return tuple(_normalize_item(item) for item in raw_items)


def _normalize_item(item: Any) -> dict[str, Any]:
    if is_dataclass(item):
        return dict(asdict(item))
    if isinstance(item, Mapping):
        return dict(item)
    raise TypeError("knowledge evidence must be a Mapping or dataclass")


def _validate_evidence(items: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    for item in items:
        if any(name not in item for name in ("key", "doc_token", "revision_id", "source_revisions")):
            raise ValueError("依赖证据字段无效")
        if (not isinstance(item["key"], str) or not item["key"].strip()
                or not isinstance(item["doc_token"], str) or not item["doc_token"].strip()):
            raise ValueError("依赖证据字段无效")
        if isinstance(item["revision_id"], bool) or not isinstance(item["revision_id"], int):
            raise ValueError("依赖证据字段无效")
        revisions = item["source_revisions"]
        if (not isinstance(revisions, dict) or not revisions
                or any(not isinstance(node, str) or not node.strip()
                       or not isinstance(revision, str) or not revision.strip()
                       for node, revision in revisions.items())):
            raise ValueError("依赖证据字段无效")
    return items


def render_dependency_record(record: DependencyRecord) -> str:
    payload = {
        "artifact_id": record.artifact_id,
        "artifact_type": record.artifact_type,
        "built_at": record.built_at,
        "evidence": [dict(item) for item in record.evidence],
    }
    return "## built_against\n```json\n" + json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n```\n"


def parse_dependency_record(markdown: str) -> DependencyRecord:
    if not isinstance(markdown, str) or markdown.count("## built_against\n") != 1:
        raise ValueError("invalid built_against envelope")
    match = re.search(r"(?ms)^## built_against\n```json\n(.*?)\n```\s*\Z", markdown)
    if match is None:
        raise ValueError("invalid built_against envelope")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as error:
        raise ValueError("invalid built_against JSON") from error
    if (not isinstance(payload, dict)
            or set(payload) != {"artifact_id", "artifact_type", "built_at", "evidence"}
            or not isinstance(payload["artifact_id"], str) or not payload["artifact_id"].strip()
            or payload["artifact_type"] not in ARTIFACT_TYPES
            or not isinstance(payload["built_at"], str) or not payload["built_at"].strip()
            or not isinstance(payload["evidence"], list)):
        raise ValueError("invalid built_against payload")
    items = tuple(_normalize_item(item) for item in payload["evidence"])
    if not items:
        raise ValueError("knowledge bundle has no evidence")
    items = _validate_evidence(items)
    return DependencyRecord(
        artifact_id=payload["artifact_id"],
        artifact_type=payload["artifact_type"],
        built_at=payload["built_at"],
        evidence=items,
    )
