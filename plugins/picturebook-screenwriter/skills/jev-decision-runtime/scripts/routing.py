"""Rules-first-match routing for Jev decision items.

A route belongs to one item (a knowledge chunk, a page, or a red line) and is
decided by that item's whole answer set, so a single route can depend on
several questions. The rules themselves (bands, order, labels) live in the
operation's policy entry; this module only computes bands and applies the
first matching rule.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, DecimalException
from typing import Any

from decision_contract import CONTENT_REMOVING_ROUTES, ContractError

# `CONTENT_REMOVING_ROUTES` is re-exported above: an exclusion must never rest
# on a question that was never answered, so an all_of rule with one of those
# routes has to have evaluated every condition it declares; a partially answered
# exclusion rule is undecided, and an undecided item falls through to the
# operation's conservative fallback.

# A question id that ends with this suffix names every answer that carries the
# text before it as a prefix.
PATTERN_SUFFIX = "*"


class RoutingError(ContractError):
    """A routing table, an answer set, or an item broke the routing contract."""


# Answer sets that cannot be read as a verdict: a routing refusal, an answer that
# is not an object, a missing primitive value, or a value that is not a number
# (`Decimal` reports the last one as an `ArithmeticError`). A malformed answer
# may cost its own item its route, but the provider call behind it has already
# been paid for, so reading one must never raise past the caller that records the
# result. `RoutingError` is spelled out even though `ContractError` is already a
# `ValueError`.
UNREADABLE_ANSWER_ERRORS = (
    RoutingError,
    KeyError,
    TypeError,
    ValueError,
    DecimalException,
)


def _matching_question_ids(question_id: str, answers: Mapping[str, Any]) -> tuple[str, ...]:
    """The answers one condition names, in the order they were returned.

    One question per active red line arrives under a dynamic id
    (`redline:<rule_id>`), so the policy addresses that family with a trailing
    `*` instead of a list it would have to regenerate whenever a red line is
    added or retired. Every other id names exactly one answer, so an exact id
    behaves exactly as it did before patterns existed.
    """

    if not question_id.endswith(PATTERN_SUFFIX):
        return (question_id,) if question_id in answers else ()
    prefix = question_id[: -len(PATTERN_SUFFIX)]
    if not prefix:
        # A bare `*` would band every answer on the item, naming evidence the
        # rule's author never wrote down.
        raise RoutingError("a question id pattern needs a prefix before '*'")
    return tuple(key for key in answers if isinstance(key, str) and key.startswith(prefix))


def probability_band(probability: Any, bands: Mapping[str, Any]) -> str:
    """Place a probability into the operation's clear / grey / risk bands.

    Comparison is Decimal because 0.35 has no exact binary representation and a
    threshold landing exactly on a band boundary must not flip on rounding.
    """

    value = Decimal(str(probability))
    if value <= Decimal(str(bands["clear_at_or_below"])):
        return "clear"
    if value >= Decimal(str(bands["risk_at_or_above"])):
        return "risk"
    return "grey"


def answer_band(answer: Any, bands: Mapping[str, Any]) -> str:
    """Band one answer.

    Only noul answers are banded. A Choice's top probability and a Score's
    position are not the yes/no likelihood a band rule is written against, so
    banding them would silently give a rule a meaning its author did not write.
    """

    if not isinstance(answer, Mapping):
        raise RoutingError("answer must be an object")
    primitive = answer.get("type")
    if primitive != "noul":
        raise RoutingError(f"only noul answers can be banded, got {primitive!r}")
    return probability_band(answer["noul"], bands)


def condition_matches(
    condition: Mapping[str, Any], answers: Mapping[str, Any], bands: Mapping[str, Any]
) -> bool:
    """Return whether one condition holds for at least one answer it names.

    A missing answer never satisfies a condition: absence must not be readable
    as evidence, in either direction. A pattern that matched no answer holds
    nothing, so it satisfies nothing either.
    """

    return any(
        answer_band(answers[question_id], bands) in condition["bands"]
        for question_id in _matching_question_ids(condition["question_id"], answers)
    )


def condition_fully_matches(
    condition: Mapping[str, Any], answers: Mapping[str, Any], bands: Mapping[str, Any]
) -> bool:
    """Return whether every answer one condition names bands into it.

    `all_of` reads each condition as a whole rather than as one answer: when a
    condition names a family of answers, a rule that clears the item only holds
    while every one of them is clear. A condition that matched nothing is
    unevaluated, never satisfied.
    """

    question_ids = _matching_question_ids(condition["question_id"], answers)
    if not question_ids:
        return False
    return all(
        answer_band(answers[question_id], bands) in condition["bands"]
        for question_id in question_ids
    )


def rule_matches(
    rule: Mapping[str, Any], answers: Mapping[str, Any], bands: Mapping[str, Any]
) -> bool:
    if "any_of" in rule:
        return any(condition_matches(item, answers, bands) for item in rule["any_of"])
    conditions = rule["all_of"]
    evaluated = [
        item for item in conditions
        if _matching_question_ids(item["question_id"], answers)
    ]
    if not evaluated:
        # An all_of rule that evaluated nothing would match vacuously, which
        # would let a *missing* answer read as a clear verdict. Refuse instead.
        # This is also what lets one rule cover items that are exempt from some
        # of its questions: the unasked conditions are skipped, not failed.
        return False
    if rule.get("route") in CONTENT_REMOVING_ROUTES and len(evaluated) != len(conditions):
        # Skipping is only sound while the rule cannot withhold content. A route
        # that drops the item has to be unanimous across every declared
        # question, so an unasked condition leaves it undecided rather than
        # satisfied.
        return False
    return all(condition_fully_matches(item, answers, bands) for item in evaluated)


def route_item(item_id: str, answers: Mapping[str, Any], operation_policy: Mapping[str, Any]) -> dict:
    """Route one item, or return the operation's conservative fallback.

    Rules are tried in declaration order and the first match wins, so a policy
    author can order the most severe rule first.
    """

    routing = operation_policy.get("routing")
    if not isinstance(routing, Mapping) or not isinstance(routing.get("bands"), Mapping):
        raise RoutingError("operation policy has no routing.bands object")
    bands = routing["bands"]
    for rule in routing.get("rules") or ():
        if rule_matches(rule, answers, bands):
            entry = {"item_id": item_id, "route": rule["route"]}
            if rule.get("label"):
                entry["label"] = rule["label"]
            return entry
    entry = {"item_id": item_id, "route": operation_policy["fallback_route"]}
    if operation_policy.get("fallback_label"):
        entry["label"] = operation_policy["fallback_label"]
    return entry


def route_items(
    answers_by_item: Mapping[str, Mapping[str, Any]], operation_policy: Mapping[str, Any]
) -> tuple[dict, ...]:
    return tuple(
        route_item(item_id, answers, operation_policy)
        for item_id, answers in answers_by_item.items()
    )
