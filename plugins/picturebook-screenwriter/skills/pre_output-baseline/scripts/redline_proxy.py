"""Cheap zero-false-negative project red-line scanner.

It produces candidates, never verdicts: a literal hit means "this text needs
review", and only the explanatory review can record a violation.
"""

from quality_gate import PROXY_SOURCE, Finding


def scan_redlines(text: str, rules: tuple[tuple[str, str, str], ...]) -> tuple[Finding, ...]:
    findings = []
    for rule_id, pattern, evidence in rules:
        if pattern and pattern in text:
            findings.append(Finding(
                id=rule_id,
                source=PROXY_SOURCE,
                severity="FAIL",
                message=f"候选命中项目红线：{pattern}",
                evidence=evidence,
            ))
    return tuple(findings)
