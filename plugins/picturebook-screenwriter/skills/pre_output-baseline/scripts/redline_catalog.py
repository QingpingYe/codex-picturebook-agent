"""Rebuild the project red-line rule catalog from authoritative knowledge pages.

The screening stage must cover every active red line, so the catalog cannot be
the set of patterns some regex happened to match: it is rebuilt from the
authority pages themselves, the machine-readable constraint blocks first and the
quoted fragments of the constraint sections as the documented fallback. A page
that declares no constraints contributes no rule, and an empty catalog is a
reported gap rather than a page with no red lines.
"""

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_KNOWLEDGE_SCRIPTS = Path(__file__).resolve().parents[2] / "knowledge-loader" / "scripts"
if str(_KNOWLEDGE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_KNOWLEDGE_SCRIPTS))

# The constraint vocabulary has exactly one authority. Re-declaring the tuples
# here would let the marking stage and the catalog stage drift apart, and a
# section protected by one is then filterable by the other.
from required_marking import (  # noqa: E402
    PROHIBITION_HEADING_MARKERS,
    PROHIBITION_PAGE_TYPES,
    REQUIRED_ALL_PAGE_TYPES,
    REQUIRED_HEADING_MARKERS,
    mark_bundle,
    page_type_of,
)

# The machine-readable constraint blocks that hold literal banned words. The
# templates put `redline_terms` on the corrections page and `banned_terms` on the
# story fingerprint page's 「禁用词与禁止写法」 section, so reading only the
# corrections block would leave those literal red lines out of the catalog.
MACHINE_DATA_BLOCK = "redline_terms"
MACHINE_DATA_BLOCKS = ("redline_terms", "banned_terms")

# A page is in scope when it is one of the prohibition page types, declares its
# own constraint headings, or is a whole-page constraint. `content-spec` is the
# last case: its data-placement row puts the banned-word list there whenever it
# does not live on the fingerprint page, and because the page carries no
# prohibition heading the machine block is the only reader that can see it.
# Other page types stay out — no template gives them a constraint, and their
# machine blocks describe page allocation — so their blocks remain invisible on
# purpose.
CONSTRAINT_PAGE_TYPES = tuple(sorted(
    set(PROHIBITION_PAGE_TYPES)
    | set(REQUIRED_HEADING_MARKERS)
    | set(REQUIRED_ALL_PAGE_TYPES)
))

_LIST_ITEM = re.compile(r"^\s*-\s*(?:\"([^\"]+)\"|'([^']+)'|(.+?))\s*$")
_FENCE_LINE = re.compile(r"^\s*(```|~~~)\s*[A-Za-z0-9_+-]*\s*$")
# A quoted fragment is a candidate banned literal, so it cannot span a line: the
# newline exclusion also keeps a fenced block's own backticks from pairing up
# into junk patterns such as "-", which would match nearly every draft.
_QUOTED = re.compile(r"[`“\"]([^`“”\"\n]{1,40})[`”\"]")
# Mirrors `required_marking`'s anchor reader, normalisation included, so a page
# both stages read is read the same way: an anchor is a constraint block whatever
# its spacing or casing, and the delimiters may sit tight against the name.
_MACHINE_DATA_ANCHOR = re.compile(r"<!--\s*machine-data:\s*([^\s>|]*)", re.IGNORECASE)

# A pattern longer than this is prose, not a literal term. The templates are
# explicit that only literal banned words belong in the machine list because a
# long sentence can never match a draft.
MAX_PATTERN_CHARS = 40


@dataclass(frozen=True)
class RedlineRule:
    rule_id: str
    pattern: str
    evidence: str
    source_key: str
    revision_id: int

    def as_triple(self) -> tuple[str, str, str]:
        return (self.rule_id, self.pattern, self.evidence)


def _anchor_name(line: str) -> str | None:
    match = _MACHINE_DATA_ANCHOR.search(line)
    if match is None:
        return None
    return match.group(1).strip().rstrip("-").strip().lower()


def _anchor_index(lines, block_name: str) -> int | None:
    wanted = block_name.strip().lower()
    for index, line in enumerate(lines):
        if _anchor_name(line) == wanted:
            return index
    return None


def _is_block_key_line(line: str, block_name: str) -> bool:
    return re.match(rf"{re.escape(block_name)}\s*:", line.strip(), re.IGNORECASE) is not None


def _fenced_body(lines, marker: str) -> list[str]:
    """The lines of a fenced block, or none when the opener is never closed.

    Mirrors `chunker`'s rule that an orphan opener is ordinary text: the block is
    then unreadable, which sends its section to the prose fallback.
    """

    for position, line in enumerate(lines):
        if line.strip().startswith(marker):
            return lines[:position]
    return []


def _block_lines(lines, block_name: str) -> list[str]:
    """The lines belonging to the block that an anchor line introduces.

    A fence opens this block only when it opens *immediately* after the anchor; a
    fence further down belongs to another section and must not be read here.
    Otherwise the block is the bare key line and list items the templates print
    under the anchor, which end at the first line that is none of those.
    """

    if lines:
        opener = _FENCE_LINE.match(lines[0])
        if opener is not None:
            return _fenced_body(lines[1:], opener.group(1))
    block: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("-") or _is_block_key_line(line, block_name):
            block.append(line)
            continue
        break
    return block


def _list_terms(lines) -> tuple[str, ...]:
    terms = []
    for line in lines:
        item = _LIST_ITEM.match(line)
        if item is None:
            continue
        term = next((group for group in item.groups() if group), "").strip()
        if term:
            terms.append(term)
    return tuple(terms)


def parse_machine_data_terms(body: str, block_name: str) -> tuple[str, ...]:
    """Read the literal terms of a `<!-- machine-data: name -->` block.

    Both shapes the templates print are read: the fenced block and the bare
    `name:` list the corrections template uses. Terms come back in page order.
    """

    lines = body.splitlines()
    index = _anchor_index(lines, block_name)
    if index is None:
        return ()
    return _list_terms(_block_lines(lines[index + 1:], block_name))


def machine_block_terms(text: str) -> tuple[str, ...]:
    """Every literal term of every machine-readable constraint block in `text`."""

    terms: list[str] = []
    for block_name in MACHINE_DATA_BLOCKS:
        terms.extend(parse_machine_data_terms(text, block_name))
    return tuple(terms)


def parse_quoted_terms(text: str) -> tuple[str, ...]:
    """Extract backticked or quoted fragments as candidate banned literals."""

    return tuple(
        match.group(1).strip()
        for match in _QUOTED.finditer(text)
        if match.group(1).strip()
    )


def _rule_id(pattern: str) -> str:
    """A stable id derived from the pattern, so the same rule keeps its id."""

    return "redline-" + hashlib.sha256(pattern.encode("utf-8")).hexdigest()[:12]


def _evidence_for(chunk) -> str:
    heading = " / ".join(chunk.heading_path)
    return f"{chunk.key} {heading}".strip()


def _is_constraint_page(chunk) -> bool:
    """Whether this chunk sits on a page the templates give constraints to."""

    return page_type_of(chunk.key) in CONSTRAINT_PAGE_TYPES


def _constraint_markers(page_type: str) -> tuple[str, ...]:
    """The heading markers `required_marking` uses to protect this page type."""

    markers = REQUIRED_HEADING_MARKERS.get(page_type, ())
    if page_type in PROHIBITION_PAGE_TYPES:
        markers = markers + PROHIBITION_HEADING_MARKERS
    return markers


def catalog_from_bundle(bundle: Any) -> tuple[RedlineRule, ...]:
    """Build the catalog, preferring a machine block over the prose fallback.

    Terms are deduplicated by pattern and the first page that contributed a
    pattern owns it, so a rule always names a real authoritative source. The
    fallback reads the quoted fragments of every section `required_marking`
    protects on that page type, not only the ones named by the generic
    prohibition vocabulary.
    """

    marked = mark_bundle(bundle)
    rules: list[RedlineRule] = []
    seen: set[str] = set()

    def add(pattern: str, chunk) -> None:
        pattern = pattern.strip()
        if not pattern or len(pattern) > MAX_PATTERN_CHARS or pattern in seen:
            return
        seen.add(pattern)
        rules.append(RedlineRule(
            rule_id=_rule_id(pattern),
            pattern=pattern,
            evidence=_evidence_for(chunk),
            source_key=chunk.key,
            revision_id=chunk.revision_id,
        ))

    for chunk in marked:
        if not _is_constraint_page(chunk):
            continue
        for term in machine_block_terms(chunk.text):
            add(term, chunk)

    for chunk in marked:
        if not _is_constraint_page(chunk) or not chunk.required:
            continue
        if machine_block_terms(chunk.text):
            # The block for this section is authoritative; its prose is not
            # scraped as well, or every quoted example would become a rule.
            continue
        heading = " / ".join(chunk.heading_path)
        if not any(marker in heading for marker in _constraint_markers(page_type_of(chunk.key))):
            continue
        for term in parse_quoted_terms(chunk.text):
            add(term, chunk)

    return tuple(rules)


def rule_triples(rules) -> tuple[tuple[str, str, str], ...]:
    """Adapt the catalog to `scan_redlines`' `(rule_id, pattern, evidence)` tuple."""

    return tuple(rule.as_triple() for rule in rules)
