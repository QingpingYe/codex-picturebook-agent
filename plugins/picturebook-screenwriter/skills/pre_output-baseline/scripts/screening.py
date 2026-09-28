"""Screening decisions: what a pre-screen may and may not conclude.

This type is deliberately separate from SemanticJudgment. A screening outcome
is a routing result derived from probabilities; a semantic judgment is an
explanatory review conclusion. Only the latter can create a finding.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_RUNTIME_SCRIPTS = Path(__file__).resolve().parents[2] / "jev-decision-runtime" / "scripts"
if str(_RUNTIME_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_SCRIPTS))

from routing import RoutingError, route_item  # noqa: E402

SCREENING_OUTCOMES = ("screened_clear", "escalate_llm", "runtime_failure")


@dataclass(frozen=True)
class ScreeningDecision:
    item_id: str
    dimension: str
    outcome: str
    probabilities: Mapping[str, float]
    label: str | None = None
    reason: str | None = None


def failure_decision(item_id: str, dimension: str, reason: str) -> ScreeningDecision:
    """Record a screening that could not run.

    A failure carries no probabilities and can never be read as a pass — there
    is nothing for it to clear on.
    """

    return ScreeningDecision(item_id, dimension, "runtime_failure", {}, None, reason)


def _noul_probabilities(answers: Mapping[str, Any]) -> dict[str, float]:
    probabilities = {}
    for question_id, answer in answers.items():
        if isinstance(answer, Mapping) and answer.get("type") == "noul":
            probabilities[str(question_id)] = float(answer["noul"])
    return probabilities


def screening_decision(
    *,
    item_id: str,
    dimension: str,
    answers: Mapping[str, Any],
    operation_policy: Mapping[str, Any],
) -> ScreeningDecision:
    """Route one dimension of one item.

    A missing or unbandable answer can never produce `screened_clear`: an empty
    answer set is recorded as a failure before any rule runs, and an answer the
    router cannot band raises here and becomes a runtime failure. An answer for
    a question the item *asked* but the model left out never reaches this
    function at all — the result contract settles that response as
    `failed`/`incomplete_response` (see `validate_answer_ids`), and the runner
    turns a non-succeeded batch into a runtime failure for every dimension in
    it. Routing itself skips questions the item never asked, which is what lets
    a closing page clear while its page-turn question is not asked.
    """

    if not answers:
        return failure_decision(item_id, dimension, "no_answers")
    try:
        route = route_item(item_id, answers, operation_policy)
    except RoutingError as error:
        return failure_decision(item_id, dimension, f"routing_error:{error}")
    outcome = "screened_clear" if route["route"] == "screened_clear" else "escalate_llm"
    return ScreeningDecision(
        item_id, dimension, outcome, _noul_probabilities(answers),
        route.get("label"), None,
    )


def may_skip_llm_review(decision: ScreeningDecision, calibration_status: str) -> bool:
    """Whether this clear verdict may reduce the plain-LLM re-check.

    While an operation is experimental its thresholds are unvalidated on this
    project's Chinese samples, so a clear verdict is only a candidate
    conclusion: it produces comparison data but the full review still runs.
    """

    return decision.outcome == "screened_clear" and calibration_status == "calibrated"


def _select(decisions: Sequence[ScreeningDecision], outcome: str) -> tuple[ScreeningDecision, ...]:
    return tuple(decision for decision in decisions if decision.outcome == outcome)


def cleared_items(decisions: Sequence[ScreeningDecision]) -> tuple[ScreeningDecision, ...]:
    return _select(decisions, "screened_clear")


def escalated_items(decisions: Sequence[ScreeningDecision]) -> tuple[ScreeningDecision, ...]:
    return _select(decisions, "escalate_llm")


def failed_items(decisions: Sequence[ScreeningDecision]) -> tuple[ScreeningDecision, ...]:
    return _select(decisions, "runtime_failure")


def summarise(decisions: Sequence[ScreeningDecision]) -> dict:
    """Report the counts and ratios spec §11.2 requires without a division error."""

    decisions = tuple(decisions)
    total = len(decisions)
    cleared = len(cleared_items(decisions))
    escalated = len(escalated_items(decisions))
    failed = len(failed_items(decisions))
    return {
        "total": total,
        "screened_clear": cleared,
        "escalated": escalated,
        "runtime_failure": failed,
        "screened_clear_ratio": (cleared / total) if total else 0.0,
        "escalation_ratio": ((escalated + failed) / total) if total else 0.0,
    }
