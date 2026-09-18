"""Cheap, deterministic collision scan between a draft and evidence."""

from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class CollisionHit:
    key: str
    term: str
    evidence_excerpt: str
    draft_excerpt: str


def check_collisions(draft: str, bundle: Mapping[str, object],
                     terms: tuple[str, ...]) -> tuple[CollisionHit, ...]:
    hits: list[CollisionHit] = []
    raw_items = bundle.get("items", ()) if isinstance(bundle, Mapping) else bundle.items
    for item in (_normalize_item(item) for item in raw_items):
        content = item["content"]
        for term in terms:
            if term in draft and term in content:
                hits.append(CollisionHit(
                    key=item["key"],
                    term=term,
                    evidence_excerpt=_excerpt(content, term),
                    draft_excerpt=_excerpt(draft, term),
                ))
    return tuple(hits)


def _excerpt(text: str, term: str, radius: int = 24) -> str:
    index = text.find(term)
    start = max(0, index - radius)
    end = min(len(text), index + len(term) + radius)
    return text[start:end].replace("\n", " ")


def _normalize_item(item: Any) -> dict[str, Any]:
    if is_dataclass(item):
        return dict(asdict(item))
    if isinstance(item, Mapping):
        return dict(item)
    raise TypeError("knowledge evidence must be a Mapping or dataclass")
