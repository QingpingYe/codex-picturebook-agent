"""Per-page text-quality dimensions for the pre-output screening."""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

_CRAFT_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "craft-benchmark-check" / "scripts"
)
if str(_CRAFT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_CRAFT_SCRIPTS))

from craft_benchmark import parse_script  # noqa: E402

DIMENSIONS = (
    "direct_moralizing",
    "age_comprehension_risk",
    "read_aloud_friction",
    "weak_page_turn_motivation",
    "emotion_told_not_shown",
)

# A closing page has nothing to turn to, so its lack of a pull is not a defect.
LAST_PAGE_EXEMPT_DIMENSIONS = ("weak_page_turn_motivation",)

# The script table uses " / " to separate the lines printed on one page.
LINE_SEPARATOR = "/"
_SENTENCE_SPLIT = re.compile(r"[.!?。！？]+")


def parse_script_pages(markdown: str) -> tuple[dict, ...]:
    """Reuse the craft benchmark's page parser rather than writing a second one."""

    return tuple(parse_script(markdown)["pages"])


def _lines(text: str) -> list[str]:
    return [line.strip() for line in str(text).split(LINE_SEPARATOR) if line.strip()]


def page_facts(text: str) -> dict:
    """Facts the model must not compute itself.

    These are context for the judgement questions, not a second craft
    benchmark: craft_benchmark.py remains the authority for the fifteen
    project metrics, and nothing here overrides its values.
    """

    lines = _lines(text)
    joined = " ".join(lines)
    sentences = [part for part in _SENTENCE_SPLIT.split(joined) if part.strip()]
    return {
        "char_count": len(joined),
        "sentence_count": len(sentences),
        "max_line_repeat": max(Counter(lines).values(), default=0),
    }


def last_page_no(pages) -> str:
    """Return the highest page number, or "" when there are no pages."""

    numbered = [page for page in pages if str(page.get("no", "")).isdigit()]
    if not numbered:
        return ""
    return max(numbered, key=lambda page: int(page["no"]))["no"]


def is_last_page(pages, page_no: str) -> bool:
    final = last_page_no(pages)
    return bool(final) and str(page_no) == final


def dimensions_for(page_no: str, *, is_last_page: bool) -> tuple[str, ...]:
    """The dimensions that apply to one page.

    A closing page has nothing to turn to, so asking whether it motivates a
    page turn would only produce a spurious finding.
    """

    if is_last_page:
        return tuple(
            dimension for dimension in DIMENSIONS
            if dimension not in LAST_PAGE_EXEMPT_DIMENSIONS
        )
    return DIMENSIONS
