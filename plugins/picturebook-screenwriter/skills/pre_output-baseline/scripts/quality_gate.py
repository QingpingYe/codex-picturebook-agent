"""Shared quality report model for the pre-output gate."""

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Finding:
    id: str
    source: str
    severity: str
    message: str
    evidence: str


@dataclass(frozen=True)
class SemanticJudgment:
    finding_id: str
    verdict: str
    rationale: str
    evidence: str


@dataclass(frozen=True)
class QualityReport:
    status: str
    findings: tuple[Finding, ...]
    blocked_reasons: tuple[str, ...]


def build_report(findings: Iterable[Finding]) -> QualityReport:
    values = tuple(findings)
    blocked = tuple(
        finding.message for finding in values
        if finding.source == "project" and finding.severity == "FAIL"
    )
    status = "blocked" if blocked else (
        "needs_user_decision"
        if any(finding.severity == "FAIL" for finding in values)
        else "passed"
    )
    return QualityReport(status, values, blocked)


def apply_judgments(
    findings: Iterable[Finding],
    judgments: Iterable[SemanticJudgment],
) -> tuple[Finding, ...]:
    by_id = {judgment.finding_id: judgment for judgment in judgments}
    result = []
    for finding in findings:
        judgment = by_id.get(finding.id)
        if judgment is None:
            result.append(finding)
            continue
        if judgment.verdict not in {"PASS", "WARN", "FAIL"}:
            raise ValueError(f"invalid verdict: {judgment.verdict}")
        result.append(Finding(
            finding.id,
            finding.source,
            judgment.verdict,
            f"{finding.message}；语义判定：{judgment.rationale}",
            judgment.evidence or finding.evidence,
        ))
    return tuple(result)


def report_to_markdown(report: QualityReport) -> str:
    lines = ["## 质量报告", "", f"状态：{report.status}", ""]
    for finding in report.findings:
        lines.append(f"- [{finding.severity}] {finding.message}；依据：{finding.evidence}")
    if report.blocked_reasons:
        lines.extend(["", "阻断原因："])
        lines.extend(f"- {reason}" for reason in report.blocked_reasons)
    return "\n".join(lines) + "\n"
