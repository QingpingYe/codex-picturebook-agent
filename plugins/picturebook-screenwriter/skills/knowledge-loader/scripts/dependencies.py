"""Creation dependency locking for authoritative Feishu evidence."""

import json
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class DependencyRecord:
    artifact_id: str
    artifact_type: str
    built_at: str
    evidence: tuple[Mapping[str, Any], ...]


def build_dependency_record(bundle: Mapping[str, Any], artifact_id: str,
                            artifact_type: str) -> DependencyRecord:
    if _bundle_offline(bundle):
        raise ValueError("离线缓存不能生成权威依赖记录")
    if not artifact_id or artifact_type not in {
        "positioning", "topic", "worldview", "character", "outline", "script"
    }:
        raise ValueError("invalid artifact id or type")
    items = tuple(_bundle_items(bundle))
    if not items:
        raise ValueError("knowledge bundle has no evidence")
    return DependencyRecord(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        built_at=bundle["fetched_at"],
        evidence=items,
    )


def _bundle_offline(bundle: Any) -> bool:
    return bool(bundle.get("offline")) if isinstance(bundle, Mapping) else bool(bundle.offline)


def _bundle_items(bundle: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(bundle, Mapping):
        raw_items = bundle.get("items", ())
    else:
        raw_items = bundle.items
    return tuple(asdict(item) if is_dataclass(item) else dict(item) for item in raw_items)


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
    prefix = "## built_against\n```json\n"
    suffix = "\n```\n"
    if not markdown.startswith(prefix) or not markdown.endswith(suffix):
        raise ValueError("invalid built_against envelope")
    payload = json.loads(markdown[len(prefix):-len(suffix)])
    return DependencyRecord(
        artifact_id=payload["artifact_id"],
        artifact_type=payload["artifact_type"],
        built_at=payload["built_at"],
        evidence=tuple(payload["evidence"]),
    )
