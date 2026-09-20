"""Read-only integrity checks for the authoritative target Wiki."""

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

PRE_OUTPUT = Path(__file__).parents[2] / "pre_output-baseline" / "scripts"
if str(PRE_OUTPUT) not in sys.path:
    sys.path.insert(0, str(PRE_OUTPUT))

from quality_gate import Finding


def _entry_value(entry: Any, field: str) -> Any:
    if isinstance(entry, Mapping):
        return entry.get(field)
    return getattr(entry, field, None)


def lint_index(index: Mapping[str, Any]) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for key, entry in index.items():
        if _entry_value(entry, "key") != key:
            findings.append(Finding(
                f"wiki-key-{key}",
                "wiki",
                "FAIL",
                "索引键与条目键不一致",
                str(entry),
            ))
        if _entry_value(entry, "status") == "needs_review":
            findings.append(Finding(
                f"wiki-review-{key}",
                "wiki",
                "WARN",
                "知识页存在待处理冲突",
                key,
            ))
    return tuple(findings)
