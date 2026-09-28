"""Side-by-side comparison of the plain-LLM and Jev-assisted execution paths."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from decision_contract import OPERATIONS, operation_policy
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
    for note in jev.get("notes") or ():
        lines.append(f"- Jev 侧记录：{note}")
    declared_only = (report.get("identity_attestation") or {}).get("declared_only") or ()
    if declared_only:
        verified = (report.get("identity_attestation") or {}).get("verified") or ()
        lines.extend([
            "",
            "case identity 核验："
            + ("运行目录核验 " + "、".join(verified) if verified
               else "本次没有任何字段被运行目录核验")
            + "；仅凭人工声明 " + "、".join(declared_only) + "。",
        ])
    for entry in report.get("calibration", ()):
        lines.extend([
            "",
            f"- 校准建议：{entry.get('operation')} "
            f"{entry.get('current')} → {entry.get('suggestion')}（{entry.get('reason')}）",
        ])
    return "\n".join(lines) + "\n"


TRACE_GLOB = "jev/*/trace/*.json"

# A waiting run never dispatched a request, so its "elapsed" is the user's own
# configuration time. Counting it would corrupt the speed comparison the whole
# report exists to produce.
UNMEASURED_STATUSES = ("waiting_for_jev_key", "waiting_for_jev_access")


def load_traces(run_dir: Any) -> tuple[dict, ...]:
    """Read the terminal measurable trace of every operation under a run dir.

    A resumed or retried operation writes one trace per attempt, and only the
    terminal attempt may contribute verdict counts: adding the attempts
    together would double the cleared and escalated totals this whole report
    rests on. An attempt that never dispatched is dropped before that choice,
    because the wait a user spent supplying a key must not displace the
    measurement an attempt that really ran produced. The failure paths carry
    null usage anyway, so nothing billable is lost.
    """

    root = Path(run_dir)
    if not root.is_dir():
        return ()
    terminal: dict[str, tuple[int, dict]] = {}
    for path in sorted(root.glob(TRACE_GLOB)):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("status") in UNMEASURED_STATUSES:
            continue
        attempt = int(path.stem) if path.stem.isdigit() else 0
        operation = path.parent.parent.name
        if operation not in terminal or attempt > terminal[operation][0]:
            terminal[operation] = (attempt, payload)
    return tuple(payload for _, payload in terminal.values())


def _sum_optional(values) -> int | None:
    present = [value for value in values if isinstance(value, int)]
    if not present:
        return None
    return sum(present)


def _sum_cost(values) -> str | None:
    """Sum the measured costs; an unmeasured cost stays unknown, never zero.

    A trace without usage is a call whose price nobody observed, so folding it
    in as `Decimal("0")` would present a bill nobody measured as a measured
    one. The zero-start sum only fixes the number of decimal places the
    contract promises.
    """

    present = [Decimal(str(value)) for value in values if value is not None]
    if not present:
        return None
    total = sum(present, Decimal("0.000000000000"))
    # `str()` switches to scientific notation for a zero total ("0E-12"),
    # which is not the plain fixed-point string the trace contract promises.
    return format(total, "f")


def jev_metrics(traces, *, measurable_only: bool = True) -> CaseMetrics:
    """Fold the traces into one measurement of the Jev-assisted path.

    The unmeasured shapes are filtered here as well as in `load_traces`,
    because a caller that assembles traces itself must not be able to put a
    credential wait or an explicit path switch into the speed sample.
    """

    traces = tuple(traces)
    if measurable_only:
        measured = tuple(
            item for item in traces
            if item.get("status") not in UNMEASURED_STATUSES
            # A run that switched back to the plain LLM mid-way measured a
            # hybrid path, so it is not a Jev-assisted sample either.
            and item.get("fallback_used") is not True
        )
    else:
        measured = traces
    if not measured:
        return CaseMetrics(path="jev_assisted")
    item_counts = [
        item.get("item_count") for item in measured
        if isinstance(item.get("item_count"), int)
    ]
    screened = _sum_optional(item.get("screened_clear_count") for item in measured)
    escalated = _sum_optional(item.get("escalated_count") for item in measured)
    failed = len([item for item in measured if item.get("status") != "succeeded"])
    notes = []
    if escalated is not None:
        notes.append(f"escalated={escalated}")
    if screened is not None:
        notes.append(f"screened_clear={screened}")
    notes.append(f"runtime_failure={failed}")
    operations = sorted({
        str(item.get("operation")) for item in measured if item.get("operation")
    })
    if operations:
        # Naming the operations makes an unbalanced pair visible: a side that
        # only ran the red-line prefilter cannot be compared on cost with a
        # side that also screened the knowledge base.
        notes.append("operations=" + ",".join(operations))
    return CaseMetrics(
        path="jev_assisted",
        elapsed_ms=_sum_optional(item.get("elapsed_ms") for item in measured),
        request_count=_sum_optional(item.get("request_count") for item in measured),
        input_tokens=_sum_optional(item.get("input_tokens") for item in measured),
        output_tokens=_sum_optional(item.get("output_tokens") for item in measured),
        cache_tokens=0,
        estimated_cost_usd=_sum_cost(
            item.get("estimated_cost_usd") for item in measured
        ),
        knowledge_items_entered=max(item_counts) if item_counts else None,
        quality_items_entered=max(item_counts) if item_counts else None,
        issues_found=(escalated + failed) if escalated is not None else None,
        misses_or_disagreements=None,
        notes=tuple(notes),
    )


_LLM_USAGE_FIELDS = (
    "elapsed_ms", "request_count", "input_tokens", "output_tokens", "cache_tokens",
    "estimated_cost_usd", "knowledge_items_entered", "quality_items_entered",
    "issues_found", "misses_or_disagreements",
)


def load_llm_usage(path: Any) -> tuple[dict, CaseMetrics]:
    """Read the user-supplied plain-LLM usage entry.

    The plugin never reads CC Switch's own store: its schema is not a stable
    public contract and reading it would hard-code a personal environment path.
    """

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("llm usage entry must be a JSON object")
    validate_identity(payload.get("identity"))
    source = str(payload.get("source", ""))
    if not source.startswith("cc-switch-manual-entry") and source != "manual":
        raise ValueError(
            "llm usage source must be a manual entry; this plugin does not read "
            "the CC Switch database"
        )
    cost = payload.get("estimated_cost_usd")
    if cost is not None and not isinstance(cost, str):
        raise ValueError(
            "estimated_cost_usd must be a quoted decimal string, for example "
            '"1.234500000000": a JSON number is parsed as a binary float, and '
            "this report is not allowed to disagree with the invoice"
        )
    values = {name: payload.get(name) for name in _LLM_USAGE_FIELDS}
    return dict(payload["identity"]), CaseMetrics(
        path="llm",
        notes=(f"model={payload.get('model_label')}", f"source={source}"),
        **values,
    )


# Escalation above this share means the thresholds are clearing too little for
# the screening to earn its cost, which is a threshold question, not a
# calibration question.
HIGH_ESCALATION_RATIO = 0.8


def _note_value(notes, name: str) -> int | None:
    for note in notes or ():
        key, separator, value = str(note).partition("=")
        if separator and key == name:
            try:
                return int(value)
            except ValueError:
                return None
    return None


def calibration_suggestions(
    policy: Mapping[str, Any], report: Mapping[str, Any]
) -> tuple[dict, ...]:
    """Suggest a calibration action per operation.

    The suggestion is advisory only. Flipping `calibration_status` changes how
    much the plain LLM re-checks, so it stays a human decision recorded in the
    policy file.
    """

    jev = report.get("jev_assisted") or {}
    notes = jev.get("notes") or ()
    escalated = _note_value(notes, "escalated")
    screened_clear = _note_value(notes, "screened_clear")
    failed = _note_value(notes, "runtime_failure") or 0
    total = None
    if escalated is not None and screened_clear is not None:
        total = escalated + screened_clear
    suggestions = []
    for operation in OPERATIONS:
        entry = operation_policy(policy, operation)
        current = entry["calibration_status"]
        suggestion = "keep_experimental"
        reason = "尚无足够的本地测量"
        if not report.get("comparable"):
            reason = "两次运行不可比较，本次数据不能用于校准"
        elif failed:
            reason = f"本次有 {failed} 项运行失败，失败项不得计入校准"
        elif total:
            ratio = escalated / total
            if ratio > HIGH_ESCALATION_RATIO:
                suggestion = "review_thresholds"
                reason = f"升级率 {ratio:.0%} 过高，先复核阈值"
            elif current == "experimental":
                suggestion = "eligible_for_calibrated"
                reason = "本次无失败且升级率可接受，可提交人工复核后转为 calibrated"
        suggestions.append({
            "operation": operation,
            "current": current,
            "suggestion": suggestion,
            "reason": reason,
        })
    return tuple(suggestions)


def _attest_identity(
    declared: Mapping[str, Any], traces: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> tuple[dict, tuple[str, ...], tuple[str, ...]]:
    """Check the declared case identity against what the run directory records.

    A trace carries the derived case id and the operation it ran — nothing else
    from the case identity. So the run can attest exactly two claims: which case
    its traces belong to, and that the declared policy version names a policy
    one of those operations really used. The other four fields have no second
    copy on disk to compare against, so they stay the user's single declaration
    for the pair and the report says so instead of implying they were verified.
    """

    attested = dict(declared)
    reasons: list[str] = []
    verified: list[str] = []
    case_ids = sorted({
        str(trace.get("benchmark_case_id"))
        for trace in traces
        if trace.get("benchmark_case_id")
    })
    if len(case_ids) == 1:
        attested["benchmark_case_id"] = case_ids[0]
        verified.append("benchmark_case_id")
    versions = {
        operation_policy(policy, str(trace.get("operation")))["policy_version"]
        for trace in traces
        if trace.get("operation") in OPERATIONS
    }
    if versions and declared.get("policy_version") in versions:
        verified.append("policy_version")
    elif versions:
        reasons.append(
            "policy_version is not the policy of any operation this run traces: "
            f"declared={declared.get('policy_version')} "
            f"run={','.join(sorted(versions))}"
        )
    return attested, tuple(reasons), tuple(verified)


def build_case_report(
    *, run_dir: Any, llm_usage_path: Any, policy: Mapping[str, Any]
) -> dict:
    """Assemble one comparison from a run directory and a manual usage entry.

    The entry carries the case identity once for both paths; whatever the run
    directory can attest then overrides that declaration, so an entry that
    describes a different run becomes a mismatch instead of a silent
    assumption.
    """

    llm_identity, llm = load_llm_usage(llm_usage_path)
    traces = load_traces(run_dir)
    jev_identity, attested_reasons, verified = _attest_identity(
        llm_identity, traces, policy
    )
    case_id = jev_identity.get("benchmark_case_id")
    # A run directory reused for a second case still holds the earlier traces.
    # They are excluded from the numbers (a stale trace must not inflate this
    # case's tokens or cost) but counted, so the mismatch stays visible.
    own = tuple(
        trace for trace in traces
        if trace.get("benchmark_case_id") in (None, case_id)
    )
    comparison_holds, comparison_reasons = comparability(llm_identity, jev_identity)
    comparable = comparison_holds and not attested_reasons
    jev = jev_metrics(own)
    report = build_report(
        identity=llm_identity, comparable=comparable,
        reasons=attested_reasons + comparison_reasons,
        llm=llm, jev_assisted=jev,
        calibration=calibration_suggestions(
            policy, {"comparable": comparable, "jev_assisted": _metrics_dict(jev)}
        ),
    )
    report["traces"] = len(own)
    report["foreign_traces"] = len(traces) - len(own)
    report["identity_attestation"] = {
        "verified": list(verified),
        "declared_only": [
            field_name for field_name in IDENTITY_FIELDS
            if field_name not in verified
        ],
    }
    return report
