"""Shared quality-report data model and resolution order for the pre-output gate."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any

# A proxy hit is a cheap-scan candidate: it says "this text matches a banned
# literal", and the explanatory review may clear it. An authoritative
# violation is a confirmed red-line breach recorded by that review; it is a
# fact about the draft, not an opinion, so nothing downstream may soften it.
PROXY_SOURCE = "project_proxy"
AUTHORITY_SOURCE = "project_authority"
BLOCKING_SOURCES = frozenset({AUTHORITY_SOURCE})

VERDICTS = ("PASS", "WARN", "FAIL")


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
    violations = tuple(
        finding for finding in values if finding.source in BLOCKING_SOURCES
    )
    blocked = tuple(
        finding.message for finding in violations if finding.severity == "FAIL"
    )
    # An authoritative finding is a recorded violation whether or not its
    # severity field still reads FAIL: a severity someone softened must not
    # turn a confirmed red-line breach into a pass.
    status = "blocked" if blocked else (
        "needs_user_decision"
        if violations or any(finding.severity == "FAIL" for finding in values)
        else "passed"
    )
    return QualityReport(status, values, blocked)


def apply_judgments(
    findings: Iterable[Finding],
    judgments: Iterable[SemanticJudgment],
) -> tuple[Finding, ...]:
    """Fold the explanatory review into the finding list.

    A judgment may clear a proxy candidate but may not change an authoritative
    violation's severity: the violation would then survive in name while
    silently ceasing to block.
    """

    by_id = {judgment.finding_id: judgment for judgment in judgments}
    result = []
    for finding in findings:
        judgment = by_id.get(finding.id)
        if judgment is None:
            result.append(finding)
            continue
        if judgment.verdict not in VERDICTS:
            raise ValueError(f"invalid verdict: {judgment.verdict}")
        if finding.source == AUTHORITY_SOURCE:
            result.append(replace(
                finding,
                message=f"{finding.message}；语义判定：{judgment.rationale}",
            ))
            continue
        result.append(Finding(
            finding.id,
            finding.source,
            judgment.verdict,
            f"{finding.message}；语义判定：{judgment.rationale}",
            judgment.evidence or finding.evidence,
        ))
    return tuple(result)


def promote_confirmed_redlines(
    findings: Iterable[Finding],
    judgments: Iterable[SemanticJudgment],
) -> tuple[Finding, ...]:
    """Turn proxy candidates the final review confirmed into violations.

    This is the only path that creates a blocking red-line finding, which is
    what keeps "the scan matched a literal" and "the draft violates a red line"
    from being the same statement.
    """

    by_id = {judgment.finding_id: judgment for judgment in judgments}
    result = []
    for finding in findings:
        judgment = by_id.get(finding.id)
        if (
            finding.source == PROXY_SOURCE
            and judgment is not None
            and judgment.verdict == "FAIL"
        ):
            result.append(replace(
                finding,
                source=AUTHORITY_SOURCE,
                message=f"{finding.message}；终审确认：{judgment.rationale}",
                evidence=judgment.evidence or finding.evidence,
            ))
            continue
        result.append(finding)
    return tuple(result)


def resolve_findings(
    findings: Iterable[Finding],
    judgments: Iterable[SemanticJudgment],
) -> tuple[Finding, ...]:
    """Apply the one supported order: judge first, then confirm red lines.

    Reversing these would let a judgment clear a finding that promotion had
    already made authoritative.
    """

    judged = apply_judgments(findings, judgments)
    return promote_confirmed_redlines(judged, judgments)


def report_to_markdown(report: QualityReport) -> str:
    lines = ["## 质量报告", "", f"状态：{report.status}", ""]
    for finding in report.findings:
        lines.append(f"- [{finding.severity}] {finding.message}；依据：{finding.evidence}")
    if report.blocked_reasons:
        lines.extend(["", "阻断原因："])
        lines.extend(f"- {reason}" for reason in report.blocked_reasons)
    return "\n".join(lines) + "\n"


def report_to_json(report: QualityReport) -> dict[str, Any]:
    payload = asdict(report)
    payload["findings"] = list(payload["findings"])
    payload["blocked_reasons"] = list(payload["blocked_reasons"])
    return payload
