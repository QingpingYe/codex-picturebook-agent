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

# The two rows that ask how much of an operation still goes to the plain LLM.
# They are not interchangeable units — a chunk of knowledge and a page
# dimension are different things — so each row reads its own operation's own
# number instead of the largest item count of whatever happened to run.
ITEMS_ENTERED_BY_OPERATION = (
    ("knowledge_relevance", "knowledge_items_entered"),
    ("text_quality_prefilter", "quality_items_entered"),
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
    """A stable digest of the shared case identity.

    Only the gate fields are digested. The digest names the shared case, so a
    key the comparability gate never reads must not change that name — and a
    missing gate field is refused here rather than digested into a name that
    looks like a case.
    """

    if not isinstance(identity, Mapping):
        raise ValueError("identity must be an object of gate fields")
    missing = [name for name in IDENTITY_FIELDS if name not in identity]
    if missing:
        raise ValueError(
            "identity is missing the gate field(s) " + ", ".join(missing)
        )
    return "sha256:" + sha256_hex(
        canonical_json({name: identity[name] for name in IDENTITY_FIELDS})
    )


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


def _cell(value: Any, side: str) -> str:
    """One table cell, naming the side when nothing was measured there."""

    return f"未测得（{side}）" if value is None else str(value)


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
    traces = report.get("traces")
    foreign = report.get("foreign_traces")
    if traces is not None or foreign is not None:
        # A run directory reused for another case keeps the earlier traces. The
        # numbers come from the traces that belong to this case, so the count
        # left out has to be readable next to them rather than remembered.
        lines.append(
            f"trace 数：{traces if traces is not None else '未统计'}"
            f"（属于其它 case：{foreign if foreign is not None else '未统计'}）"
        )
    run_ids = report.get("runs") or ()
    if len(run_ids) > 1:
        lines.append("运行目录包含的 run_id：" + "、".join(str(value) for value in run_ids))
    lines.append("| 指标 | 普通 LLM | Jev 辅助 |")
    lines.append("| --- | --- | --- |")
    llm = report.get("llm", {})
    jev = report.get("jev_assisted", {})
    for name in METRIC_NAMES:
        # A missing measurement has to say which side is missing it, or a row
        # reading `100 | None` leaves the reader to guess what was measured.
        lines.append(
            f"| {name} | {_cell(llm.get(name), '普通 LLM')} "
            f"| {_cell(jev.get(name), 'Jev 辅助')} |"
        )
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

    The terminal attempt is chosen per run, not per operation name. A directory
    that holds two runs of one operation is two measurements of the same task,
    and collapsing them onto one key would drop one of the two from the report
    — while summing them would double it. Both are wrong in the same way: the
    report is about one run, so the two stay separate and `build_case_report`
    refuses to compare a directory whose traces name more than one run.
    """

    root = Path(run_dir)
    if not root.is_dir():
        return ()
    terminal: dict[tuple[str, str], tuple[int, dict]] = {}
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
        key = (str(payload.get("run_id") or ""), path.parent.parent.name)
        if key not in terminal or attempt > terminal[key][0]:
            terminal[key] = (attempt, payload)
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


def _measured(traces, measurable_only: bool = True) -> tuple:
    """The traces that are measurements of the Jev-assisted path.

    A run that switched back to the plain LLM mid-way measured a hybrid path,
    and a run that never got past the credential wait measured the user's own
    configuration time: neither is a Jev-assisted sample.
    """

    traces = tuple(traces)
    if not measurable_only:
        return traces
    return tuple(
        item for item in traces
        if item.get("status") not in UNMEASURED_STATUSES
        and item.get("fallback_used") is not True
    )


def operation_metrics(traces, *, measurable_only: bool = True) -> tuple[dict, ...]:
    """One measurement per run and operation the Jev side really ran.

    Two runs of one operation are two measurements, not one. The report lists
    them separately so a run directory that holds traces from more than one run
    shows that in its numbers instead of quietly adding two attempts at the
    same work together, and so a reader can see which operation produced which
    number: the two operations count different units and only one of them may
    have been run.
    """

    groups: dict[tuple[str, str], list[dict]] = {}
    for item in _measured(traces, measurable_only):
        key = (str(item.get("run_id") or ""), str(item.get("operation") or ""))
        groups.setdefault(key, []).append(item)
    entries = []
    for (run_id, operation), items in sorted(groups.items()):
        entries.append({
            "run_id": run_id,
            "operation": operation,
            "traces": len(items),
            "item_count": _sum_optional(item.get("item_count") for item in items),
            "screened_clear_count": _sum_optional(
                item.get("screened_clear_count") for item in items
            ),
            "escalated_count": _sum_optional(
                item.get("escalated_count") for item in items
            ),
            # A batch that did not succeed decided nothing, so its verdict
            # counts stay zero and it is reported as a failure of its own.
            "runtime_failure": len(
                [item for item in items if item.get("status") != "succeeded"]
            ),
            # A succeeded batch decided at least one item, so both counts being
            # zero on a succeeded trace means they were never recorded — the
            # shape the `resume` path leaves, because that entry point has no
            # operation routing to hand the runner. The numbers beside it are a
            # lower bound then, and the advice has to know that.
            "traces_without_verdicts": len([
                item for item in items
                if item.get("status") == "succeeded"
                and not item.get("screened_clear_count")
                and not item.get("escalated_count")
            ]),
            "elapsed_ms": _sum_optional(item.get("elapsed_ms") for item in items),
            "request_count": _sum_optional(
                item.get("request_count") for item in items
            ),
            "input_tokens": _sum_optional(item.get("input_tokens") for item in items),
            "output_tokens": _sum_optional(
                item.get("output_tokens") for item in items
            ),
            "estimated_cost_usd": _sum_cost(
                item.get("estimated_cost_usd") for item in items
            ),
        })
    return tuple(entries)


def _items_entered(per_operation, operation: str) -> int | None:
    """How many items one operation handed to the plain LLM in this run.

    The number is the operation's own escalated count: the items the screen
    could not settle by itself and passed on, which is what the spec's "进入
    LLM 的项数" row asks for. `item_count` is not that number — it counts the
    authority pages a request cited — so it is reported per operation beside
    this one instead of standing in for it.
    """

    values = [
        entry.get("escalated_count") for entry in per_operation
        if entry.get("operation") == operation
        and isinstance(entry.get("escalated_count"), int)
    ]
    return sum(values) if values else None


def _entered_by_metric(per_operation) -> dict:
    """The two "what still goes to the plain LLM" rows, from one mapping.

    `ITEMS_ENTERED_BY_OPERATION` is the mapping both rows are built from, so
    the pair cannot drift away from the table a reader consults.
    """

    return {
        metric: _items_entered(per_operation, operation)
        for operation, metric in ITEMS_ENTERED_BY_OPERATION
    }


def jev_metrics(traces, *, measurable_only: bool = True) -> CaseMetrics:
    """Fold the traces into one measurement of the Jev-assisted path.

    The unmeasured shapes are filtered here as well as in `load_traces`,
    because a caller that assembles traces itself must not be able to put a
    credential wait or an explicit path switch into the speed sample.
    """

    measured = _measured(traces, measurable_only)
    if not measured:
        return CaseMetrics(path="jev_assisted")
    per_operation = operation_metrics(measured, measurable_only=False)
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
    run_ids = sorted({
        str(item.get("run_id")) for item in measured if item.get("run_id")
    })
    if len(run_ids) > 1:
        # Two runs of one task in one directory are two measurements, so the
        # sum is named for what it is instead of reading as a single run.
        notes.append(f"runs={len(run_ids)}")
    without_verdicts = sum(
        entry["traces_without_verdicts"] for entry in per_operation
    )
    if without_verdicts:
        # Name the hole where the numbers are: those traces carry a zero pair
        # that was never decided, so the escalation rows beside them are lower
        # bounds rather than measurements.
        notes.append(f"traces_without_verdicts={without_verdicts}")
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
        **_entered_by_metric(per_operation),
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


def calibration_suggestions(
    policy: Mapping[str, Any], report: Mapping[str, Any]
) -> tuple[dict, ...]:
    """Suggest a calibration action for every operation this run measured.

    The advice is per operation and from that operation's own numbers. A run
    that escalated nearly every knowledge chunk and almost nothing in the page
    pre-screen must not lend one blended rate to both: read as a single rate it
    would tell the operation whose thresholds are too tight that it may be
    promoted, which is the opposite of what its own numbers say. An operation
    this run never measured gets no suggestion at all, because "no
    measurement" is not evidence in either direction — the report names the
    operations it ran separately.

    An operation that holds a succeeded trace whose counts were never recorded
    gets no promotion either: a batch settled that way always decided at least
    one item, so the visible escalation share is a lower bound, and a bound may
    not be read as "cleared enough to stop re-checking".

    The suggestion is advisory only. Flipping `calibration_status` changes how
    much the plain LLM re-checks, so it stays a human decision recorded in the
    policy file.
    """

    suggestions = []
    for measured in report.get("jev_operations") or ():
        operation = str(measured.get("operation"))
        if operation not in OPERATIONS:
            # A trace naming an operation this build does not know cannot be
            # priced against a policy, so it produces no advice. It is still
            # listed in `jev_operations`, where the reader can see it.
            continue
        screened_clear = measured.get("screened_clear_count")
        escalated = measured.get("escalated_count")
        failed = measured.get("runtime_failure") or 0
        without_verdicts = measured.get("traces_without_verdicts") or 0
        total = (
            screened_clear + escalated
            if isinstance(screened_clear, int) and isinstance(escalated, int)
            else 0
        )
        entry = operation_policy(policy, operation)
        current = entry["calibration_status"]
        suggestion = "keep_experimental"
        reason = "尚无足够的本地测量"
        if not report.get("comparable"):
            reason = "两次运行不可比较，本次数据不能用于校准"
        elif without_verdicts:
            # Fail closed: the missing counts are the reason the ratio beside
            # them is wrong rather than merely thin, so no promotion — and no
            # "review the thresholds" either — is read out of this run.
            reason = (
                f"该 operation 有 {without_verdicts} 条已成功的 trace 没有记下判定计数"
                "（例如由 resume 继续的批次），升级率只是下界，不能用于校准"
            )
        elif failed:
            reason = f"该 operation 本次有 {failed} 项运行失败，失败项不得计入校准"
        elif total:
            ratio = escalated / total
            if ratio > HIGH_ESCALATION_RATIO:
                suggestion = "review_thresholds"
                reason = f"该 operation 本次升级率 {ratio:.0%} 过高，先复核阈值"
            elif current == "experimental":
                suggestion = "eligible_for_calibrated"
                reason = "该 operation 本次无失败且升级率可接受，可提交人工复核后转为 calibrated"
        suggestions.append({
            "operation": operation,
            "run_id": measured.get("run_id"),
            "measured_items": total,
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
    elif not case_ids:
        # The directory holds no trace at all, or only traces that name no case
        # id. Either way nothing on disk attests the case the entry declares,
        # and a report that called that comparable would be comparing a
        # measurement with a declaration it never checked — one of the two
        # shapes the fail-closed identity gate exists to catch.
        reasons.append(
            "no trace in the run directory carries the declared "
            f"benchmark_case_id ({declared.get('benchmark_case_id')}), so the "
            "declared case id is unverified"
        )
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
    # One report describes one run. A directory that holds two runs of the same
    # case — the same draft screened twice under two run ids — cannot say which
    # of them the hand-copied plain-LLM usage describes, so the pair is refused
    # rather than compared against a sum of both. The run ids are reported and
    # each run's own numbers stay visible in `jev_operations`.
    run_ids = sorted({
        str(trace.get("run_id")) for trace in own if trace.get("run_id")
    })
    run_reasons = (
        (
            "the run directory holds traces from "
            f"{len(run_ids)} runs ({', '.join(run_ids)}), so it cannot say "
            "which run the plain-LLM usage entry describes",
        )
        if len(run_ids) > 1
        else ()
    )
    comparison_holds, comparison_reasons = comparability(llm_identity, jev_identity)
    comparable = comparison_holds and not attested_reasons and not run_reasons
    jev = jev_metrics(own)
    per_operation = [dict(entry) for entry in operation_metrics(own)]
    report = build_report(
        identity=llm_identity, comparable=comparable,
        reasons=attested_reasons + run_reasons + comparison_reasons,
        llm=llm, jev_assisted=jev,
        calibration=calibration_suggestions(
            policy, {"comparable": comparable, "jev_operations": per_operation}
        ),
    )
    report["jev_operations"] = per_operation
    report["traces"] = len(own)
    report["foreign_traces"] = len(traces) - len(own)
    report["runs"] = run_ids
    report["identity_attestation"] = {
        "verified": list(verified),
        "declared_only": [
            field_name for field_name in IDENTITY_FIELDS
            if field_name not in verified
        ],
    }
    return report
