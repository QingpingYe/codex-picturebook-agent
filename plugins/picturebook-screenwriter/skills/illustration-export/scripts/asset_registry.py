"""Versioned asset registry for illustration preproduction."""

import json
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class AssetRecord:
    asset_id: str
    asset_type: str
    name: str
    status: str
    image_path: str | None
    built_against: Mapping[str, int]


def render_registry(records: list[AssetRecord]) -> str:
    payload = [
        {
            "asset_id": record.asset_id,
            "asset_type": record.asset_type,
            "name": record.name,
            "status": record.status,
            "image_path": record.image_path,
            "built_against": dict(record.built_against),
        }
        for record in records
    ]
    return (
        "## asset-registry\n```json\n"
        + json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n```\n"
    )


def parse_registry(markdown: str) -> tuple[AssetRecord, ...]:
    prefix = "## asset-registry\n```json\n"
    suffix = "\n```\n"
    if not markdown.startswith(prefix) or not markdown.endswith(suffix):
        raise ValueError("invalid asset registry envelope")

    payload = json.loads(markdown[len(prefix):-len(suffix)])
    return tuple(
        AssetRecord(
            item["asset_id"],
            item["asset_type"],
            item["name"],
            item["status"],
            item["image_path"],
            item["built_against"],
        )
        for item in payload
    )
