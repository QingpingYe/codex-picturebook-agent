"""Cheap zero-false-negative project red-line scanner."""

from quality_gate import Finding


def scan_redlines(text: str, rules: tuple[tuple[str, str, str], ...]) -> tuple[Finding, ...]:
    findings = []
    for rule_id, pattern, evidence in rules:
        if pattern and pattern in text:
            findings.append(Finding(
                id=rule_id,
                source="project",
                severity="FAIL",
                message=f"命中项目红线：{pattern}",
                evidence=evidence,
            ))
    return tuple(findings)
