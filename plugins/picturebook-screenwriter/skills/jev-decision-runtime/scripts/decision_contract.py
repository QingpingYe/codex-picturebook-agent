"""Shared Jev decision-layer contracts: request, result, context, and policy."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REQUEST_SCHEMA = "pb-jev-request-v1"
RESULT_SCHEMA = "pb-jev-result-v1"
TRACE_SCHEMA = "pb-decision-trace-v1"
CONTEXT_SCHEMA = "pb-decision-context-v1"
POLICY_SCHEMA = "pb-jev-policy-v1"

OPERATIONS = ("knowledge_relevance", "text_quality_prefilter")
PRIMITIVES = ("noul", "choice", "score")

# A route describes one item — a knowledge chunk, a page, or a red line — not
# one question, because a single route can depend on several answers. The
# vocabulary is fixed here so the result contract is complete; the per-operation
# routing tables that produce these routes arrive with each operation's policy.
ROUTE_VALUES = ("include", "exclude_soft", "screened_clear", "escalate_llm", "needs_user_choice")

# The band vocabulary a routing table may place probabilities into.
BAND_VALUES = ("clear", "grey", "risk")

RESULT_STATUSES = (
    "succeeded",
    "failed",
    "waiting_for_jev_key",
    "waiting_for_jev_access",
    "outcome_unknown",
)
WAITING_STATUSES = ("waiting_for_jev_key", "waiting_for_jev_access")

CREDENTIAL_STATUSES = (
    "unchecked",
    "available",
    "waiting_for_jev_key",
    "waiting_for_jev_access",
)
EXECUTION_MODES = ("llm", "jev_assisted")

MOVING_MODEL_ALIASES = ("jev-latest", "jev-preview")

# A single operation may need more than one request (a batch of items). The
# optional operation_instance keeps each batch's operation directory, lease,
# pending call, and result distinct.
_INSTANCE_RE = re.compile(r"[A-Za-z0-9_-]+")

# run_id and operation_instance are both interpolated into the on-disk
# operation directory, so neither may carry a path separator or a parent
# reference.
_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")

# Mirrors scripts/package_check.py so both credential sweeps agree on what a
# credential-shaped field name looks like. Suffix matching keeps the token
# counters (input_tokens / output_tokens) out of the net.
_CREDENTIAL_SUFFIXES = ("api_key", "apikey", "authorization", "password", "secret", "token")


def is_valid_run_id(value: Any) -> bool:
    """True when `value` is safe to interpolate into the operation directory.

    Callers that name a run themselves (a CLI deriving one from a directory,
    for instance) check this before building a request, so the refusal can name
    the flag that fixes it instead of surfacing as a request-contract error.
    """

    return isinstance(value, str) and bool(_RUN_ID_RE.fullmatch(value))


class ContractError(ValueError):
    """A request, result, context, or policy payload violated the contract."""


def _require_object(payload: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ContractError(f"{label} must be a JSON object")
    return payload


def _require_nonempty_str(payload: Mapping[str, Any], key: str, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label}.{key} must be a non-empty string")
    return value


def _require_finite_number(payload: Mapping[str, Any], key: str, label: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{label}.{key} must be a number")
    if not math.isfinite(float(value)):
        raise ContractError(f"{label}.{key} must be a finite number")
    return float(value)


def _require_probability(payload: Mapping[str, Any], key: str, label: str) -> float:
    value = _require_finite_number(payload, key, label)
    if not 0.0 <= value <= 1.0:
        raise ContractError(f"{label}.{key} must be between 0 and 1")
    return value


def _validate_probability_map(value: Any, label: str) -> None:
    if not isinstance(value, Mapping) or not value:
        raise ContractError(f"{label}.probabilities must be a non-empty object")
    total = 0.0
    for key, probability in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ContractError(f"{label}.probabilities keys must be non-empty strings")
        total += _require_probability(
            {key: probability}, key, f"{label}.probabilities"
        )
    if abs(total - 1.0) > 1e-6:
        raise ContractError(f"{label}.probabilities must sum to 1 (got {total})")


def assert_no_credential_fields(payload: Any, label: str = "payload") -> None:
    """Reject any field whose name looks like a credential carrier."""

    if isinstance(payload, Mapping):
        for key, child in payload.items():
            key_name = str(key).lower()
            if key_name.endswith(_CREDENTIAL_SUFFIXES):
                raise ContractError(f"{label}.{key} is a credential-shaped field")
            assert_no_credential_fields(child, f"{label}.{key}")
    elif isinstance(payload, (list, tuple)):
        for index, child in enumerate(payload):
            assert_no_credential_fields(child, f"{label}[{index}]")


def _validate_context_ref(ref: Any, label: str) -> None:
    ref = _require_object(ref, label)
    _require_nonempty_str(ref, "ref_id", label)
    _require_nonempty_str(ref, "kind", label)
    revisions = ref.get("revisions")
    if not isinstance(revisions, Mapping) or not revisions:
        raise ContractError(f"{label}.revisions must be a non-empty object")
    for key, value in revisions.items():
        if not isinstance(key, str) or not key.strip():
            raise ContractError(f"{label}.revisions keys must be non-empty strings")
        if not isinstance(value, str) or not value.strip():
            raise ContractError(f"{label}.revisions values must be non-empty strings")


def _validate_question(question_id: Any, question: Any) -> None:
    if not isinstance(question_id, str) or not question_id.strip():
        raise ContractError("request.questions keys must be non-empty strings")
    label = f"question {question_id!r}"
    question = _require_object(question, label)
    primitive = question.get("type")
    if primitive not in PRIMITIVES:
        raise ContractError(f"{label}.type must be one of {PRIMITIVES}")
    instructions = question.get("instructions")
    if isinstance(instructions, str):
        if not instructions.strip():
            raise ContractError(f"{label}.instructions must be non-empty")
    elif not isinstance(instructions, (Mapping, list)):
        raise ContractError(f"{label}.instructions must be a string, object, or array")
    criteria = question.get("criteria")
    if primitive == "choice":
        if not isinstance(criteria, Mapping) or not criteria:
            raise ContractError(f"{label} is a choice and needs non-empty object criteria")
    elif primitive == "score":
        if not isinstance(criteria, list) or len(criteria) < 2:
            raise ContractError(f"{label} is a score and needs at least two level descriptions")
        if len(criteria) > 10:
            raise ContractError(f"{label} is a score and accepts at most ten level descriptions")


def validate_request(payload: Any) -> None:
    payload = _require_object(payload, "request")
    if payload.get("schema_version") != REQUEST_SCHEMA:
        raise ContractError(f"request.schema_version must be {REQUEST_SCHEMA!r}")
    run_id = _require_nonempty_str(payload, "run_id", "request")
    if not is_valid_run_id(run_id):
        raise ContractError(
            "request.run_id must match [A-Za-z0-9][A-Za-z0-9_-]{0,127}: it becomes "
            "part of the on-disk operation directory"
        )
    operation = _require_nonempty_str(payload, "operation", "request")
    if operation not in OPERATIONS:
        raise ContractError(f"request.operation is not supported yet: {operation}")
    model = _require_nonempty_str(payload, "model", "request")
    if model in MOVING_MODEL_ALIASES:
        raise ContractError("request.model must be a pinned version id, not a moving alias")
    _require_nonempty_str(payload, "policy_version", "request")
    state = payload.get("state")
    if not isinstance(state, (str, Mapping, list)):
        raise ContractError("request.state must be a string, object, or array")
    questions = payload.get("questions")
    if not isinstance(questions, Mapping) or not questions:
        raise ContractError("request.questions must be a non-empty object")
    for question_id, question in questions.items():
        _validate_question(question_id, question)
    refs = payload.get("context_refs")
    if not isinstance(refs, list) or not refs:
        raise ContractError("request.context_refs must be a non-empty array")
    for index, ref in enumerate(refs):
        _validate_context_ref(ref, f"request.context_refs[{index}]")
    _require_nonempty_str(payload, "benchmark_case_id", "request")
    if "operation_instance" in payload:
        instance = payload["operation_instance"]
        if not isinstance(instance, str) or not _INSTANCE_RE.fullmatch(instance):
            raise ContractError(
                "request.operation_instance must match [A-Za-z0-9_-]+ when present"
            )
    assert_no_credential_fields(payload, "request")


def _validate_answer(question_id: str, answer: Any) -> None:
    label = f"result.answers[{question_id!r}]"
    answer = _require_object(answer, label)
    primitive = answer.get("type")
    if primitive not in PRIMITIVES:
        raise ContractError(f"{label}.type must be one of {PRIMITIVES}")
    if primitive == "noul":
        _require_probability(answer, "noul", label)
        return
    if primitive == "choice":
        choice = _require_nonempty_str(answer, "choice", label)
        probabilities = answer.get("probabilities")
        _validate_probability_map(probabilities, label)
        if choice not in probabilities:
            raise ContractError(f"{label}.choice must be one of its probabilities")
        # The reported choice and the distribution have to agree: a response
        # whose "choice" is not the most probable option is internally
        # inconsistent, and an inconsistent response is not a verdict.
        best = max(float(value) for value in probabilities.values())
        if float(probabilities[choice]) < best - 1e-9:
            raise ContractError(
                f"{label}.choice must be the most probable option; "
                f"{choice!r} is not the argmax of its probabilities"
            )
        _require_probability(answer, "confidence", label)
        return
    _require_finite_number(answer, "score", label)
    _validate_probability_map(answer.get("probabilities"), label)
    legend = answer.get("legend")
    if not isinstance(legend, Mapping) or not legend:
        raise ContractError(f"{label}.legend must be a non-empty object")
    _require_probability(answer, "confidence", label)


def _validate_usage(usage: Any) -> None:
    if not isinstance(usage, Mapping):
        raise ContractError("result.usage must be an object")
    for key in ("input_tokens", "output_tokens"):
        value = usage.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ContractError(f"result.usage.{key} must be null or a non-negative integer")


def _validate_routes(routes: Any) -> None:
    """Routes are per *item* (a knowledge chunk, a page, a red line), not per question.

    One item's route is computed by the operation's routing rules from that
    item's full answer set, so a single route can depend on several questions.
    """

    if not isinstance(routes, list):
        raise ContractError("result.routes must be an array")
    for index, route in enumerate(routes):
        label = f"result.routes[{index}]"
        route = _require_object(route, label)
        _require_nonempty_str(route, "item_id", label)
        value = _require_nonempty_str(route, "route", label)
        if value not in ROUTE_VALUES:
            raise ContractError(f"{label}.route must be one of {ROUTE_VALUES}")
        if "label" in route:
            _require_nonempty_str(route, "label", label)


def validate_result(payload: Any) -> None:
    payload = _require_object(payload, "result")
    if payload.get("schema_version") != RESULT_SCHEMA:
        raise ContractError(f"result.schema_version must be {RESULT_SCHEMA!r}")
    _require_nonempty_str(payload, "run_id", "result")
    operation = _require_nonempty_str(payload, "operation", "result")
    if operation not in OPERATIONS:
        raise ContractError(f"result.operation is not supported yet: {operation}")
    if payload.get("provider") != "typesafe":
        raise ContractError("result.provider must be 'typesafe'")
    _require_nonempty_str(payload, "requested_model", "result")
    _require_nonempty_str(payload, "policy_version", "result")
    status = payload.get("status")
    if status not in RESULT_STATUSES:
        raise ContractError(f"result.status must be one of {RESULT_STATUSES}")

    resolved_model = payload.get("resolved_model")
    if status == "succeeded":
        if not isinstance(resolved_model, str) or not resolved_model.strip():
            raise ContractError("a succeeded result must report a resolved_model")
        answers = payload.get("answers")
        if not isinstance(answers, Mapping) or not answers:
            raise ContractError("a succeeded result must carry answers")
        for question_id, answer in answers.items():
            _validate_answer(str(question_id), answer)
    else:
        if resolved_model is not None and (
            not isinstance(resolved_model, str) or not resolved_model.strip()
        ):
            raise ContractError("result.resolved_model must be null or a non-empty string")
        if payload.get("answers"):
            raise ContractError("a non-succeeded result must not carry answers")

    routes = payload.get("routes")
    _validate_routes(routes)
    if status != "succeeded" and routes:
        raise ContractError("a non-succeeded result must not carry routes")
    if status in WAITING_STATUSES and payload.get("trace"):
        raise ContractError("a waiting result must not carry a trace")
    if not isinstance(payload.get("trace"), Mapping):
        raise ContractError("result.trace must be an object")
    _validate_usage(payload.get("usage"))
    assert_no_credential_fields(payload, "result")


def validate_answer_ids(request: Any, result: Any) -> None:
    """Reject a response whose answer ids differ from the submitted questions.

    The provider's 200 body is untrusted input: a body that is not an object,
    or whose `answers` is not an object of string ids, carries no answer set to
    compare. Reading one of those shapes with `.get` / `set` / `sorted` would
    raise `AttributeError` or `TypeError` *after* the call has been paid for, so
    an ill-shaped envelope is reported as the same `ContractError` an incomplete
    response gets.
    """

    if not isinstance(result, Mapping):
        raise ContractError("a provider response must be a JSON object")
    answers = result.get("answers")
    if not isinstance(answers, Mapping):
        raise ContractError("a provider response must carry an object of answers")
    for question_id in answers:
        if not isinstance(question_id, str):
            raise ContractError("answer ids must be strings")
    expected = set(request["questions"])
    actual = set(answers)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise ContractError(f"result is missing answers for: {missing}")
    if unknown:
        raise ContractError(f"result carries answers for unknown questions: {unknown}")


POLICY_STATUSES = ("experimental", "calibrated")
_DECIMAL_STRING = re.compile(r"^\d+(\.\d+)?$")


def _require_decimal_string(payload: Mapping[str, Any], key: str, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not _DECIMAL_STRING.fullmatch(value):
        raise ContractError(f"{label}.{key} must be a plain decimal string")
    return value


def _validate_question_templates(templates: Any, label: str) -> None:
    if not isinstance(templates, Mapping) or not templates:
        raise ContractError(f"{label}.question_templates must be a non-empty object")
    for question_key, template in templates.items():
        if not isinstance(question_key, str) or not question_key.strip():
            raise ContractError(f"{label}.question_templates keys must be non-empty strings")
        _validate_question(question_key, template)


def _validate_condition(
    condition: Any, position: int, rule_label: str, keyword: str, templates: Mapping[str, Any]
) -> None:
    label = f"{rule_label}.{keyword}[{position}]"
    condition = _require_object(condition, label)
    question_id = _require_nonempty_str(condition, "question_id", label)
    if question_id not in templates:
        raise ContractError(
            f"{label}.question_id must name a declared question template, got {question_id!r}"
        )
    # Only noul answers carry a probability a band can be computed from, so a
    # rule that tests a choice or a score would raise while routing and abort
    # the whole operation. Refuse the policy up front rather than discovering
    # it mid-flight.
    primitive = templates[question_id].get("type")
    if primitive != "noul":
        raise ContractError(
            f"{label}.question_id must name a noul question template: "
            f"only noul answers can be banded, but {question_id!r} is {primitive!r}"
        )
    bands = condition.get("bands")
    if not isinstance(bands, list) or not bands:
        raise ContractError(f"{label}.bands must be a non-empty array")
    for band in bands:
        if band not in BAND_VALUES:
            raise ContractError(f"{label}.bands must be drawn from {BAND_VALUES}")


def _validate_rule(rule: Any, index: int, label: str, templates: Mapping[str, Any]) -> None:
    rule_label = f"{label}.routing.rules[{index}]"
    rule = _require_object(rule, rule_label)
    route = _require_nonempty_str(rule, "route", rule_label)
    if route not in ROUTE_VALUES:
        raise ContractError(f"{rule_label}.route must be one of {ROUTE_VALUES}")
    if "label" in rule:
        _require_nonempty_str(rule, "label", rule_label)
    has_any = "any_of" in rule
    has_all = "all_of" in rule
    if has_any == has_all:
        raise ContractError(f"{rule_label} must declare exactly one of any_of or all_of")
    keyword = "any_of" if has_any else "all_of"
    conditions = rule[keyword]
    if not isinstance(conditions, list) or not conditions:
        raise ContractError(f"{rule_label}.{keyword} must be a non-empty array")
    for position, condition in enumerate(conditions):
        _validate_condition(condition, position, rule_label, keyword, templates)


def _validate_routing_table(entry: Mapping[str, Any], label: str) -> None:
    """Validate the per-operation routing schema.

    Phase 1 required this table to be empty, because a routing rule cannot be
    expressed before the questions it tests exist. This replaces that guard
    with the real schema; the question templates and the routing rules that
    reference them must now be declared together.
    """

    templates = entry.get("question_templates")
    _validate_question_templates(templates, label)

    routing = entry.get("routing")
    if not isinstance(routing, Mapping):
        raise ContractError(f"{label}.routing must be an object")
    bands = _require_object(routing.get("bands"), f"{label}.routing.bands")
    clear = _require_decimal_string(bands, "clear_at_or_below", f"{label}.routing.bands")
    risk = _require_decimal_string(bands, "risk_at_or_above", f"{label}.routing.bands")
    for key, value in (("clear_at_or_below", clear), ("risk_at_or_above", risk)):
        if not 0.0 <= float(value) <= 1.0:
            raise ContractError(f"{label}.routing.bands.{key} must be between 0 and 1")
    if float(clear) >= float(risk):
        raise ContractError(
            f"{label}.routing.bands.clear_at_or_below must be below risk_at_or_above, "
            "otherwise no grey band exists"
        )
    rules = routing.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ContractError(f"{label}.routing.rules must be a non-empty array")
    for index, rule in enumerate(rules):
        _validate_rule(rule, index, label, templates)


def validate_policy(payload: Any) -> None:
    payload = _require_object(payload, "policy")
    if payload.get("schema_version") != POLICY_SCHEMA:
        raise ContractError(f"policy.schema_version must be {POLICY_SCHEMA!r}")
    model = _require_nonempty_str(payload, "pinned_model", "policy")
    if model in MOVING_MODEL_ALIASES:
        raise ContractError("policy.pinned_model must be a pinned version id, not a moving alias")
    if payload.get("output_tokens_free") is not True:
        raise ContractError("policy.output_tokens_free must be true")
    snapshot = _require_object(payload.get("price_snapshot"), "policy.price_snapshot")
    _require_decimal_string(
        snapshot, "price_usd_per_million_input_tokens", "policy.price_snapshot"
    )
    _require_nonempty_str(snapshot, "snapshot_date", "policy.price_snapshot")

    operations = payload.get("operations")
    if not isinstance(operations, Mapping):
        raise ContractError("policy.operations must be an object")
    for operation in OPERATIONS:
        if operation not in operations:
            raise ContractError(f"policy.operations is missing the {operation!r} entry")
    for operation, entry in operations.items():
        label = f"policy.operations[{operation!r}]"
        if operation not in OPERATIONS:
            raise ContractError(f"{label} is not a supported operation")
        entry = _require_object(entry, label)
        _require_nonempty_str(entry, "policy_version", label)
        status = _require_nonempty_str(entry, "calibration_status", label)
        if status not in POLICY_STATUSES:
            raise ContractError(f"{label}.calibration_status must be one of {POLICY_STATUSES}")
        fallback = _require_nonempty_str(entry, "fallback_route", label)
        if fallback not in ROUTE_VALUES:
            raise ContractError(f"{label}.fallback_route must be one of {ROUTE_VALUES}")
        _validate_routing_table(entry, label)
        if "fallback_label" in entry:
            _require_nonempty_str(entry, "fallback_label", label)
        if "max_items_per_request" in entry:
            value = entry["max_items_per_request"]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ContractError(
                    f"{label}.max_items_per_request must be a positive integer"
                )


def default_policy_path() -> Path:
    """Return the policy file that ships with this skill."""

    return Path(__file__).resolve().parents[1] / "references" / "decision-policies.json"


def load_policy(path: Any) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ContractError(f"unable to read policy file: {error}") from error
    validate_policy(payload)
    return payload


def operation_policy(policy: Mapping[str, Any], operation: str) -> dict:
    if operation not in OPERATIONS:
        raise ContractError(f"operation is not supported yet: {operation}")
    entry = (policy.get("operations") or {}).get(operation)
    if not isinstance(entry, Mapping):
        raise ContractError(f"policy has no entry for operation {operation!r}")
    resolved = dict(entry)
    resolved.setdefault("max_items_per_request", 10)
    return resolved


SELECTION_STATUSES = ("confirmed",)
ATTEMPT_STATUSES = ("pending", "in_flight", "failed", "outcome_unknown")

INITIAL_RESUME_CURSOR = "after_execution_choice"
OPERATION_CURSOR_PREFIX = "after_operation:"


def operation_cursor(operation_id: str) -> str:
    return f"{OPERATION_CURSOR_PREFIX}{operation_id}"


def validate_resume_cursor(value: str) -> None:
    if value == INITIAL_RESUME_CURSOR:
        return
    if value.startswith(OPERATION_CURSOR_PREFIX) and len(value) > len(OPERATION_CURSOR_PREFIX):
        return
    raise ContractError(f"unknown decision_context.resume_cursor: {value!r}")


def validate_pending_call(payload: Any) -> None:
    payload = _require_object(payload, "pending_call")
    _require_nonempty_str(payload, "operation_id", "pending_call")
    operation = _require_nonempty_str(payload, "operation", "pending_call")
    if operation not in OPERATIONS:
        raise ContractError(f"pending_call.operation is not supported yet: {operation}")
    _require_nonempty_str(payload, "request_fingerprint", "pending_call")
    _require_nonempty_str(payload, "policy_version", "pending_call")
    _require_nonempty_str(payload, "requested_model", "pending_call")
    attempt_status = _require_nonempty_str(payload, "attempt_status", "pending_call")
    if attempt_status not in ATTEMPT_STATUSES:
        raise ContractError(f"pending_call.attempt_status must be one of {ATTEMPT_STATUSES}")
    refs = payload.get("input_refs")
    if not isinstance(refs, list) or not refs:
        raise ContractError("pending_call.input_refs must be a non-empty array")
    for index, ref in enumerate(refs):
        _validate_context_ref(ref, f"pending_call.input_refs[{index}]")
    assert_no_credential_fields(payload, "pending_call")


def validate_decision_context(payload: Any) -> None:
    payload = _require_object(payload, "decision_context")
    if payload.get("schema_version") != CONTEXT_SCHEMA:
        raise ContractError(f"decision_context.schema_version must be {CONTEXT_SCHEMA!r}")
    mode = payload.get("mode")
    if mode not in EXECUTION_MODES:
        raise ContractError(f"decision_context.mode must be one of {EXECUTION_MODES}")
    selection_status = payload.get("selection_status")
    if selection_status not in SELECTION_STATUSES:
        raise ContractError(
            f"decision_context.selection_status must be one of {SELECTION_STATUSES}"
        )
    credential_status = payload.get("credential_status")
    if credential_status not in CREDENTIAL_STATUSES:
        raise ContractError(
            f"decision_context.credential_status must be one of {CREDENTIAL_STATUSES}"
        )
    validate_resume_cursor(_require_nonempty_str(payload, "resume_cursor", "decision_context"))
    _require_nonempty_str(payload, "selected_at", "decision_context")
    if not isinstance(payload.get("external_text_processing_acknowledged"), bool):
        raise ContractError(
            "decision_context.external_text_processing_acknowledged must be a boolean"
        )
    pending_call = payload.get("pending_call")
    if pending_call is not None:
        validate_pending_call(pending_call)
    assert_no_credential_fields(payload, "decision_context")


def build_decision_context(
    *,
    mode: str,
    external_text_processing_acknowledged: bool,
    selected_at: str,
    resume_cursor: str = INITIAL_RESUME_CURSOR,
    credential_status: str = "unchecked",
    pending_call: Any = None,
) -> dict:
    context = {
        "schema_version": CONTEXT_SCHEMA,
        "mode": mode,
        "selection_status": "confirmed",
        "credential_status": credential_status,
        "resume_cursor": resume_cursor,
        "pending_call": pending_call,
        "selected_at": selected_at,
        "external_text_processing_acknowledged": bool(external_text_processing_acknowledged),
    }
    validate_decision_context(context)
    return context
