"""Mark the chunks that must never be filtered out of the model context."""

from __future__ import annotations

import re
import sys
from dataclasses import replace
from pathlib import Path

from chunker import Chunk, chunk_bundle, evidence_field

# The page-type vocabulary has exactly one authority. Importing it (rather than
# re-declaring it) means a page type added upstream is recognised here instead
# of silently falling through to the conservative fallback.
KNOWLEDGE_STORE_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "feishu-knowledge-store" / "scripts"
)
if str(KNOWLEDGE_STORE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(KNOWLEDGE_STORE_SCRIPTS))

from shared_schema import PAGE_TYPES  # noqa: E402

# A whole page of this type is a hard constraint: the content specification IS
# the requirement, so every chunk of it is required.
REQUIRED_ALL_PAGE_TYPES = frozenset({"content-spec"})

# Section headings that carry hard constraints, per page type. These mirror the
# 必须章节 lists in wiki-ingest/references/structured-output-templates.md.
REQUIRED_HEADING_MARKERS: dict[str, tuple[str, ...]] = {
    "worldview": ("创作红线不变量",),
    "characters": ("创作边界",),
    "corrections": ("强制性禁止条目", "身体语言禁止词汇", "红线机器可读块"),
    "creation-standards": ("创作红线",),
    "golden-sentence-registry": ("金句规则",),
    "story-fingerprint-spec": ("禁用词", "禁止写法"),
}

# Machine-readable constraint blocks. These are matched against every page type
# because a block that exists to be machine-matched is a constraint by
# construction, not descriptive prose.
REQUIRED_MACHINE_DATA_BLOCKS = (
    "redline_terms",
    "banned_terms",
    "episodes",
    "props",
    "rejected_shells",
    "dimensions",
    "thresholds",
    "quality_gates",
    "batch_diversity",
    "scale_contact",
)

_HEADING_LINE = re.compile(r"^#{1,6}\s+(.*?)\s*$")


def page_type_of(key: str) -> str:
    """Return the page type, which is the last segment of a three-part key."""

    parts = str(key).split("/")
    return parts[-1] if len(parts) == 3 else ""


def _all_required(chunks, reason: str):
    return tuple(replace(chunk, required=True, required_reason=reason) for chunk in chunks)


def _swallowed_heading_reason(text: str, markers: tuple[str, ...]) -> str | None:
    """Look for a hard-constraint heading inside the text of one chunk.

    A heading normally opens a chunk of its own, so the heading path identifies
    the section and no text scan is needed. A page that lost a closing fence
    breaks that: the next fence marker closes the orphan block and every heading
    in between ends up inside a single chunk, so the heading path no longer
    names the hard constraints while the heading text is still there. Scanning
    the text can only over-mark (a section that merely quotes one of these
    headings becomes required), which costs context instead of dropping a red
    line.
    """

    for line in text.splitlines():
        match = _HEADING_LINE.match(line)
        if not match:
            continue
        for marker in markers:
            if marker in match.group(1):
                return f"heading:{marker}"
    return None


def _chunk_reason(chunk: Chunk, markers: tuple[str, ...]) -> str | None:
    if not chunk.heading_path:
        # An unheaded preamble cannot be classified, so it is never filtered.
        return "unclassified_preamble"
    # The machine-data anchor is checked first because it is the narrower claim:
    # a block that exists to be machine-matched is a constraint by construction,
    # and the audit trail should name the block rather than whichever heading
    # happens to sit above it.
    for block in REQUIRED_MACHINE_DATA_BLOCKS:
        if f"machine-data: {block}" in chunk.text:
            return f"machine_data:{block}"
    if markers:
        heading = " / ".join(chunk.heading_path)
        for marker in markers:
            if marker in heading:
                return f"heading:{marker}"
        return _swallowed_heading_reason(chunk.text, markers)
    return None


def mark_required(
    chunks,
    *,
    page_type: str,
    status: str,
    index_synced: bool,
    declared_required: bool = False,
):
    """Return the chunks with the required flag set.

    Every branch that cannot positively classify a chunk marks it required. The
    asymmetry is deliberate: a false required chunk costs context, a false soft
    chunk can drop a hard constraint.
    """

    chunks = tuple(chunks)
    if declared_required:
        return _all_required(chunks, "caller_declared")
    if status != "published":
        return _all_required(chunks, "source_status_not_published")
    if not index_synced:
        return _all_required(chunks, "index_not_synced")
    if page_type not in PAGE_TYPES:
        return _all_required(chunks, "unknown_page_type")
    if page_type in REQUIRED_ALL_PAGE_TYPES:
        return _all_required(chunks, f"page_type:{page_type}")
    markers = REQUIRED_HEADING_MARKERS.get(page_type, ())
    reasons = [_chunk_reason(chunk, markers) for chunk in chunks]
    # Splitting an oversized section keeps the key and the heading path but not
    # the anchor that made it required: only the first part carries the
    # machine-data marker, and only it still opens with the red-line heading.
    # One required part therefore makes its whole section required.
    section_reason: dict[tuple[str, tuple[str, ...]], str] = {}
    for chunk, reason in zip(chunks, reasons):
        if reason is not None:
            section_reason.setdefault((chunk.key, chunk.heading_path), reason)
    result = []
    for chunk, reason in zip(chunks, reasons):
        effective = reason
        if effective is None:
            effective = section_reason.get((chunk.key, chunk.heading_path))
        result.append(replace(
            chunk, required=effective is not None, required_reason=effective
        ))
    return tuple(result)


def mark_bundle(bundle, *, declared_page_types=(), declared_keys=()):
    """Chunk and mark every item of an evidence bundle."""

    declared_types = frozenset(declared_page_types)
    declared = frozenset(declared_keys)
    marked: list[Chunk] = []
    for item in bundle.get("items", ()):
        key = str(evidence_field(item, "key", ""))
        page_type = page_type_of(key)
        chunks = chunk_bundle({"items": (item,)})
        marked.extend(mark_required(
            chunks,
            page_type=page_type,
            status=str(evidence_field(item, "status", "published")),
            index_synced=bool(evidence_field(item, "index_synced", True)),
            declared_required=key in declared or page_type in declared_types,
        ))
    return tuple(marked)
