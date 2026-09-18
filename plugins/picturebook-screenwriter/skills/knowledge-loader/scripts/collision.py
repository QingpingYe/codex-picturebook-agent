"""Cheap, deterministic collision scan between a draft and evidence."""

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class CollisionHit:
    key: str
    term: str
    evidence_excerpt: str
    draft_excerpt: str


def check_collisions(draft: str, bundle: Mapping[str, object],
                     terms: tuple[str, ...]) -> tuple[CollisionHit, ...]:
    hits: list[CollisionHit] = []
    for item in bundle["items"]:
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
