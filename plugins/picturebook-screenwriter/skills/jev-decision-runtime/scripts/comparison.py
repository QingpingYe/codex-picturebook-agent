"""Side-by-side comparison of the plain-LLM and Jev-assisted execution paths."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from telemetry import canonical_json, sha256_hex

COMPARISON_SCHEMA = "pb-path-comparison-v1"

# Two runs may only be compared when they describe the same work. Anything less
# than all of these makes the numbers describe different tasks.
IDENTITY_FIELDS = (
    "benchmark_case_id",
    "input_revision_vector_sha256",
    "draft_sha256",
    "policy_version",
    "rule_version",
    "artifact_params",
)

CALIBRATION_STATUSES = ("experimental", "calibrated")

CALIBRATION_SUGGESTIONS = (
    "keep_experimental",
    "review_thresholds",
    "eligible_for_calibrated",
)

# The metric pairs the spec's comparison table requires, in report order.
METRIC_NAMES = (
    "elapsed_ms",
    "request_count",
    "input_tokens",
    "output_tokens",
    "cache_tokens",
    "estimated_cost_usd",
    "knowledge_items_entered",
    "quality_items_entered",
    "issues_found",
    "misses_or_disagreements",
)


def validate_identity(payload: Any) -> None:
    """Refuse an identity that cannot answer "was this the same work?"."""

    if not isinstance(payload, Mapping):
        raise ValueError("identity must be an object")
    for field_name in IDENTITY_FIELDS:
        if field_name not in payload:
            raise ValueError(f"identity is missing {field_name!r}")
    for field_name in IDENTITY_FIELDS:
        if field_name == "artifact_params":
            continue
        value = payload[field_name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"identity.{field_name} must be a non-empty string")
    params = payload["artifact_params"]
    if not isinstance(params, Mapping) or not params:
        raise ValueError("identity.artifact_params must be a non-empty object")


def identity_digest(identity: Mapping[str, Any]) -> str:
    """A stable digest of the shared case identity."""

    return "sha256:" + sha256_hex(canonical_json(dict(identity)))


def comparability(
    llm_identity: Mapping[str, Any], jev_identity: Mapping[str, Any]
) -> tuple[bool, tuple[str, ...]]:
    """Decide whether two runs may be placed side by side.

    Every mismatch is reported rather than short-circuiting, so a user fixing
    one field does not have to re-run to discover the next. A field that is
    absent or null on either side is a hole, not a match: two runs that both
    omit the same field do not describe the same work.
    """

    reasons = []
    for field_name in IDENTITY_FIELDS:
        left = llm_identity.get(field_name)
        right = jev_identity.get(field_name)
        if left is None:
            reasons.append(f"{field_name} is missing from the llm identity")
            continue
        if right is None:
            reasons.append(f"{field_name} is missing from the jev_assisted identity")
            continue
        if canonical_json(left) != canonical_json(right):
            reasons.append(
                f"{field_name} differs: llm={canonical_json(left)} "
                f"jev_assisted={canonical_json(right)}"
            )
    return (not reasons), tuple(reasons)


@dataclass(frozen=True)
class CaseMetrics:
    path: str
    elapsed_ms: int | None = None
    request_count: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_tokens: int | None = None
    estimated_cost_usd: str | None = None
    knowledge_items_entered: int | None = None
    quality_items_entered: int | None = None
    issues_found: int | None = None
    misses_or_disagreements: int | None = None
    notes: tuple[str, ...] = ()


def _metrics_dict(metrics: CaseMetrics) -> dict:
    payload = asdict(metrics)
    payload["notes"] = list(payload["notes"])
    return payload


def build_report(
    *,
    identity: Mapping[str, Any],
    comparable: bool,
    reasons: Sequence[str],
    llm: CaseMetrics,
    jev_assisted: CaseMetrics,
    calibration: Sequence[Mapping[str, Any]],
) -> dict:
    return {
        "schema_version": COMPARISON_SCHEMA,
        "identity": dict(identity),
        "identity_digest": identity_digest(identity),
        "comparable": bool(comparable),
        "comparability_reasons": list(reasons),
        "llm": _metrics_dict(llm),
        "jev_assisted": _metrics_dict(jev_assisted),
        "calibration": [dict(entry) for entry in calibration],
    }


def _decimal_or_none(value: Any) -> Decimal | None:
    """Parse a metric as a finite decimal; never guess a value from prose."""

    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def describe_metric(name: str, llm_value: Any, jev_value: Any) -> str:
    """Describe the Jev side of one metric relative to the plain-LLM side.

    The caller's row label carries the metric name, so this only renders the
    movement: a missing value reads `unknown` and an empty baseline reads
    `n/a` instead of being folded into a ratio that looks measured.
    """

    if llm_value is None or jev_value is None:
        return "unknown"
    llm_number = _decimal_or_none(llm_value)
    jev_number = _decimal_or_none(jev_value)
    if llm_number is None or jev_number is None:
        return f"{llm_value} → {jev_value}"
    if llm_number == 0 or jev_number == 0:
        return f"{llm_value} → {jev_value} (n/a)"
    return f"{llm_value} → {jev_value} ({jev_number / llm_number:.2f}×)"


def report_to_markdown(report: Mapping[str, Any]) -> str:
    lines = ["## 路径对比", ""]
    if not report.get("comparable"):
        lines.append("**两次运行不可比较，下列数值仅供排查，不得作为结论。**")
        lines.append("")
        lines.append("不可比较原因：")
        lines.extend(f"- {reason}" for reason in report.get("comparability_reasons", ()))
        lines.append("")
    lines.append(f"case digest: {report.get('identity_digest', '')}")
    lines.append("")
    lines.append("| 指标 | 普通 LLM | Jev 辅助 |")
    lines.append("| --- | --- | --- |")
    llm = report.get("llm", {})
    jev = report.get("jev_assisted", {})
    for name in METRIC_NAMES:
        lines.append(f"| {name} | {llm.get(name)} | {jev.get(name)} |")
    lines.append("")
    for name in METRIC_NAMES:
        lines.append(f"- {name}: {describe_metric(name, llm.get(name), jev.get(name))}")
    for entry in report.get("calibration", ()):
        lines.extend([
            "",
            f"- 校准建议：{entry.get('operation')} "
            f"{entry.get('current')} → {entry.get('suggestion')}（{entry.get('reason')}）",
        ])
    return "\n".join(lines) + "\n"
