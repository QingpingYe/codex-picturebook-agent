"""Rebuild the project red-line rule catalog from authoritative knowledge pages.

The screening stage must cover every active red line, so the catalog cannot be
the set of patterns some regex happened to match: it is rebuilt from the
authority pages themselves, the machine-readable `redline_terms` block first
and the quoted fragments of the prohibition sections as the documented
fallback. A page that carries no prohibition contributes no rule, and an empty
catalog is a reported gap rather than a page with no red lines.
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

# The prohibition vocabulary has exactly one authority. Re-declaring the tuples
# here would let the marking stage and the catalog stage drift apart, and a
# section protected by one is then filterable by the other.
from required_marking import (  # noqa: E402
    PROHIBITION_HEADING_MARKERS,
    PROHIBITION_PAGE_TYPES,
    mark_bundle,
    page_type_of,
)

MACHINE_DATA_BLOCK = "redline_terms"

_LIST_ITEM = re.compile(r"^\s*-\s*(?:\"([^\"]+)\"|'([^']+)'|(.+?))\s*$")
_FENCE = re.compile(r"```[A-Za-z]*\n(.*?)\n```", re.DOTALL)
_QUOTED = re.compile(r"[`“\"]([^`“”\"]{1,40})[`”\"]")

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


def parse_machine_data_terms(body: str, block_name: str) -> tuple[str, ...]:
    """Read the literal terms following a `<!-- machine-data: name -->` anchor."""

    anchor = f"<!-- machine-data: {block_name} -->"
    index = body.find(anchor)
    if index == -1:
        return ()
    match = _FENCE.search(body, index + len(anchor))
    if match is None:
        return ()
    terms = []
    for line in match.group(1).splitlines():
        if not line.lstrip().startswith("-"):
            # Skips the block's own `redline_terms:` key line and any comment.
            continue
        item = _LIST_ITEM.match(line)
        if item is None:
            continue
        term = next((group for group in item.groups() if group), "").strip()
        if term:
            terms.append(term)
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


def _declares_prohibitions(chunk) -> bool:
    """Whether this chunk sits on a page the templates give prohibitions to.

    The vocabulary is page-type-scoped, so a `redline_terms` block or a
    prohibition-named section on any other page type is content, not a red line.
    """

    return page_type_of(chunk.key) in PROHIBITION_PAGE_TYPES


def catalog_from_bundle(bundle: Any) -> tuple[RedlineRule, ...]:
    """Build the catalog, preferring the machine block over the prose fallback.

    Terms are deduplicated by pattern and the first page that contributed a
    pattern owns it, so a rule always names a real authoritative source.
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
        if not _declares_prohibitions(chunk):
            continue
        for term in parse_machine_data_terms(chunk.text, MACHINE_DATA_BLOCK):
            add(term, chunk)

    for chunk in marked:
        if not _declares_prohibitions(chunk) or not chunk.required:
            continue
        if parse_machine_data_terms(chunk.text, MACHINE_DATA_BLOCK):
            # The block for this section is authoritative; its prose is not
            # scraped as well, or every quoted example would become a rule.
            continue
        heading = " / ".join(chunk.heading_path)
        if not any(marker in heading for marker in PROHIBITION_HEADING_MARKERS):
            continue
        for term in parse_quoted_terms(chunk.text):
            add(term, chunk)

    return tuple(rules)


def rule_triples(rules) -> tuple[tuple[str, str, str], ...]:
    """Adapt the catalog to `scan_redlines`' `(rule_id, pattern, evidence)` tuple."""

    return tuple(rule.as_triple() for rule in rules)
