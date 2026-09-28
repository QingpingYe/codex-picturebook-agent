"""Split authoritative knowledge page bodies into addressable chunks."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# A chunk has to stay well inside the provider's "state plus longest question"
# budget, so an oversized section is split at paragraph boundaries, and a
# single paragraph longer than the cap is sliced rather than sent whole.
MAX_CHUNK_CHARS = 1600

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_PARAGRAPH = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    key: str
    doc_token: str
    revision_id: int
    heading_path: tuple[str, ...]
    text: str
    required: bool = False
    required_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "key": self.key,
            "doc_token": self.doc_token,
            "revision_id": self.revision_id,
            "heading_path": list(self.heading_path),
            "text": self.text,
            "required": self.required,
            "required_reason": self.required_reason,
        }


def evidence_field(item: Any, name: str, default: Any = None) -> Any:
    """Read a KnowledgeEvidence field from either a dataclass or a mapping."""

    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _split_long(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    buffer = ""
    for paragraph in _PARAGRAPH.split(text):
        candidate = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        if len(candidate) > limit and buffer:
            parts.append(buffer)
            buffer = paragraph
            continue
        buffer = candidate
    if buffer:
        parts.append(buffer)
    chunks: list[str] = []
    for part in parts:
        if len(part) <= limit:
            chunks.append(part)
            continue
        chunks.extend(
            part[index:index + limit] for index in range(0, len(part), limit)
        )
    return chunks


def _has_closing_fence(lines: list[str], start: int, marker: str) -> bool:
    """A fence only opens a block when something later closes it.

    Pages are hand-edited and lose a closing fence easily. An unterminated
    opener used to swallow every later heading, which silently dropped the
    required mark from whole hard-constraint sections, so an orphan opener is
    treated as ordinary text instead.
    """

    return any(line.strip().startswith(marker) for line in lines[start:])


def chunk_body(body: str, *, max_chars: int = MAX_CHUNK_CHARS) -> list[dict]:
    """Split a page body at Markdown headings, then at paragraph boundaries.

    Headings inside fenced blocks are content, not structure: knowledge pages
    carry machine-readable YAML in fences, and a '#' opening a YAML comment must
    not be read as a section heading.
    """

    sections: list[dict] = []
    stack: list[tuple[int, str]] = []
    current: list[str] = []
    current_path: tuple[str, ...] = ()
    fence: str | None = None

    def flush() -> None:
        nonlocal current
        text = "\n".join(current).strip("\n")
        if text.strip():
            for part in _split_long(text, max_chars):
                sections.append({"heading_path": list(current_path), "text": part})
        current = []

    lines = body.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            current.append(line)
            continue
        opener = _FENCE.match(line)
        if opener and _has_closing_fence(lines, index + 1, opener.group(1)):
            fence = opener.group(1)
            current.append(line)
            continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading.group(2).strip()))
            current_path = tuple(title for _, title in stack)
            current.append(line)
            continue
        current.append(line)

    flush()
    return sections


def chunk_evidence(item: Any, *, max_chars: int = MAX_CHUNK_CHARS) -> tuple[Chunk, ...]:
    key = evidence_field(item, "key", "")
    doc_token = evidence_field(item, "doc_token", "")
    revision_id = evidence_field(item, "revision_id", 0)
    body = evidence_field(item, "content", "") or ""
    chunks = []
    for ordinal, section in enumerate(chunk_body(body, max_chars=max_chars)):
        chunks.append(Chunk(
            chunk_id=f"{key}#{ordinal:03d}",
            key=key,
            doc_token=doc_token,
            revision_id=revision_id,
            heading_path=tuple(section["heading_path"]),
            text=section["text"],
        ))
    return tuple(chunks)


def chunk_bundle(bundle: Any, *, max_chars: int = MAX_CHUNK_CHARS) -> tuple[Chunk, ...]:
    items = bundle.get("items", ()) if isinstance(bundle, Mapping) else getattr(bundle, "items", ())
    chunks: list[Chunk] = []
    for item in items:
        chunks.extend(chunk_evidence(item, max_chars=max_chars))
    return tuple(chunks)
