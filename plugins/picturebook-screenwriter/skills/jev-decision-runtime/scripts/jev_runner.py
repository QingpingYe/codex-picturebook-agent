"""Shared Jev decision runner: layout, atomic persistence, and execution."""

from __future__ import annotations

import copy
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from decision_contract import (
    ContractError,
    build_decision_context,
    default_policy_path,
    load_policy,
    operation_cursor,
    operation_policy,
    validate_answer_ids,
    validate_decision_context,
    validate_request,
    validate_result,
)
from jev_client import (
    API_KEY_ENV,
    JevClient,
    JevClientError,
    UrllibTransport,
)
from routing import RoutingError, route_item
from telemetry import (
    DecisionTrace,
    Stopwatch,
    estimate_usage,
    now_iso,
    request_fingerprint,
    sha256_hex,
    canonical_json,
)

RESULT_STATUSES_WITH_TRACE = ("succeeded", "failed", "outcome_unknown")
_CREDENTIAL_STATUS_FOR_OUTCOME = {
    "succeeded": "available",
    "failed": "available",
    "outcome_unknown": "available",
    "waiting_for_jev_key": "waiting_for_jev_key",
    "waiting_for_jev_access": "waiting_for_jev_access",
}
_ATTEMPT_STATUS_FOR_OUTCOME = {
    "succeeded": None,
    "failed": "failed",
    "outcome_unknown": "outcome_unknown",
    "waiting_for_jev_key": "pending",
    "waiting_for_jev_access": "pending",
}


@dataclass(frozen=True)
class RunnerConfig:
    run_dir: Path
    policy: Mapping[str, Any]
    execution_mode: str = "jev_assisted"
    holder: str = "primary"
    lease_ttl_minutes: int = 15


class LeaseHeld(RuntimeError):
    """Another runner holds an unexpired lease for this operation."""


@dataclass(frozen=True)
class Lease:
    operation_id: str
    holder: str
    started_at: str
    expires_at: str

    def to_dict(self) -> dict:
        return {
            "operation_id": self.operation_id,
            "holder": self.holder,
            "started_at": self.started_at,
            "expires_at": self.expires_at,
        }


def _lease_expired(lease: Mapping[str, Any], reference: datetime) -> bool:
    try:
        expires_at = datetime.fromisoformat(str(lease["expires_at"]))
    except (KeyError, TypeError, ValueError):
        return True
    if expires_at.tzinfo is None:
        return True
    return expires_at <= reference


def _reference_time(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def acquire_lease(config: RunnerConfig, operation_id: str, now: datetime | None = None) -> Lease:
    """Take the per-operation execution lease, creating it exclusively.

    This is a same-host local-file lease. It does not coordinate across hosts.
    """

    path = lease_path(config.run_dir, operation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    reference = _reference_time(now)
    lease = Lease(
        operation_id=operation_id,
        holder=config.holder,
        started_at=reference.isoformat(timespec="milliseconds"),
        expires_at=(reference + timedelta(minutes=config.lease_ttl_minutes)).isoformat(
            timespec="milliseconds"
        ),
    )
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        existing = read_json(path)
        if existing is None:
            raise LeaseHeld(f"{operation_id} has an unreadable lease file")
        if not _lease_expired(existing, reference):
            raise LeaseHeld(
                f"{operation_id} is held by {existing.get('holder')!r} "
                f"until {existing.get('expires_at')!r}"
            )
        write_atomic(path, lease.to_dict())
        return lease
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(lease.to_dict(), stream, ensure_ascii=False, sort_keys=True)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return lease


def release_lease(config: RunnerConfig, operation_id: str, holder: str) -> None:
    path = lease_path(config.run_dir, operation_id)
    existing = read_json(path)
    if existing is None:
        return
    if existing.get("holder") != holder:
        raise LeaseHeld(
            f"{operation_id} lease is held by {existing.get('holder')!r}, not {holder!r}"
        )
    path.unlink(missing_ok=True)


def operation_id_for(run_id: str, operation: str, instance: str = "") -> str:
    base = f"{run_id}-{operation}"
    return f"{base}-{instance}" if instance else base


def operation_id_from_request(request: Mapping[str, Any]) -> str:
    return operation_id_for(
        request["run_id"], request["operation"], request.get("operation_instance", "")
    )


def operation_dir(run_dir: Any, operation_id: str) -> Path:
    return Path(run_dir) / "jev" / operation_id


def request_path(run_dir: Any, operation_id: str) -> Path:
    return operation_dir(run_dir, operation_id) / "request.json"


def pending_path(run_dir: Any, operation_id: str) -> Path:
    return operation_dir(run_dir, operation_id) / "pending.json"


def lease_path(run_dir: Any, operation_id: str) -> Path:
    return operation_dir(run_dir, operation_id) / "lease.json"


def result_path(run_dir: Any, operation_id: str) -> Path:
    return operation_dir(run_dir, operation_id) / "result.json"


def trace_path(run_dir: Any, operation_id: str, attempt: int) -> Path:
    return operation_dir(run_dir, operation_id) / "trace" / f"{attempt:04d}.json"


def context_path(run_dir: Any) -> Path:
    return Path(run_dir) / "decision-context.json"


def read_json(path: Any) -> dict | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def write_atomic(path: Any, payload: Mapping[str, Any]) -> None:
    """temp + os.replace, so a reader never observes a half-written terminal file."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def build_pending_call(request: Mapping[str, Any]) -> dict:
    return {
        "operation_id": operation_id_from_request(request),
        "operation": request["operation"],
        "request_fingerprint": request_fingerprint(request),
        "policy_version": request["policy_version"],
        "requested_model": request["model"],
        "attempt_status": "pending",
        "input_refs": [copy.deepcopy(ref) for ref in request["context_refs"]],
    }


def _answers_by_item(answers: Mapping[str, Any]) -> dict[str, dict]:
    """Group flat `<item_id>::<question_id>` answers back into one dict per item.

    Answer keys are the transport's only requirement: every answer must carry
    the same id the question used, so the grouping is entirely mechanical.
    """

    grouped: dict[str, dict] = {}
    for question_ref, answer in answers.items():
        item_id, separator, question_id = str(question_ref).partition("::")
        if not separator:
            raise ContractError(
                f"answer key {question_ref!r} must be '<item_id>::<question_id>'"
            )
        grouped.setdefault(item_id, {})[question_id] = answer
    return grouped


def _routes_for(
    status: str, answers: Mapping[str, Any], operation_policy_entry: Mapping[str, Any]
) -> tuple[dict, ...]:
    """Route each answered item using the operation's policy.

    A failed or incomplete operation produces no routes: an unrouted item must
    be treated as needing review, never as cleared. That is also why an answer
    set the routing engine cannot read produces no routes instead of an
    exception: the provider call behind those answers has already been paid for,
    so a malformed answer may cost its own item a route but must still leave the
    operation with a terminal result.
    """

    if status != "succeeded" or not answers:
        return ()
    try:
        grouped = _answers_by_item(answers)
    except ContractError:
        return ()
    routes: list[dict] = []
    for item_id, item_answers in grouped.items():
        if not item_id:
            continue
        try:
            routes.append(route_item(item_id, item_answers, operation_policy_entry))
        except RoutingError:
            continue
    return tuple(routes)


def build_result(
    *,
    request: Mapping[str, Any],
    status: str,
    operation_policy_entry: Mapping[str, Any],
    payload: Mapping[str, Any] | None = None,
    error_class: str | None = None,
    attempts: int = 0,
    elapsed_ms: int = 0,
    trace: Mapping[str, Any] | None = None,
    price_usd_per_million_input_tokens: str = "0.042",
) -> dict:
    answers = {}
    usage = {"input_tokens": None, "output_tokens": None}
    resolved_model = None
    if status == "succeeded" and payload is not None:
        answers = dict(payload.get("answers") or {})
        input_tokens, output_tokens, _ = estimate_usage(
            payload.get("usage"), price_usd_per_million_input_tokens
        )
        usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
        resolved_model = payload.get("model")
    routes = _routes_for(status, answers, operation_policy_entry)
    result = {
        "schema_version": "pb-jev-result-v1",
        "run_id": request["run_id"],
        "operation": request["operation"],
        "provider": "typesafe",
        "requested_model": request["model"],
        "resolved_model": resolved_model,
        "policy_version": request["policy_version"],
        "answers": answers,
        "routes": list(routes),
        "usage": usage,
        "trace": dict(trace or {}) if status in RESULT_STATUSES_WITH_TRACE else {},
        "status": status,
    }
    if error_class is not None:
        result["error_class"] = error_class
    validate_result(result)
    return result


def read_decision_context(run_dir: Any) -> dict | None:
    return read_json(context_path(run_dir))


def build_decision_context_for_run(
    *,
    mode: str,
    external_text_processing_acknowledged: bool,
    selected_at: str | None = None,
) -> dict:
    return build_decision_context(
        mode=mode,
        external_text_processing_acknowledged=external_text_processing_acknowledged,
        selected_at=selected_at or now_iso(),
    )


def sync_decision_context(
    config: RunnerConfig,
    *,
    credential_status: str,
    pending_call: Mapping[str, Any] | None,
    resume_cursor: str | None = None,
) -> dict:
    context = read_decision_context(config.run_dir)
    if context is None:
        context = build_decision_context_for_run(
            mode=config.execution_mode,
            external_text_processing_acknowledged=True,
        )
    updated = copy.deepcopy(context)
    updated["credential_status"] = credential_status
    updated["pending_call"] = copy.deepcopy(pending_call) if pending_call else None
    if resume_cursor is not None:
        updated["resume_cursor"] = resume_cursor
    validate_decision_context(updated)
    write_atomic(context_path(config.run_dir), updated)
    return updated


def trace_for(
    request,
    config,
    *,
    status,
    attempts,
    error_class,
    resolved_model,
    usage,
    elapsed_ms,
    started_at,
    finished_at,
) -> dict | None:
    if status not in RESULT_STATUSES_WITH_TRACE:
        return None
    snapshot = config.policy["price_snapshot"]
    input_tokens, output_tokens, cost = estimate_usage(
        usage, snapshot["price_usd_per_million_input_tokens"]
    )
    trace = DecisionTrace(
        run_id=request["run_id"],
        benchmark_case_id=request["benchmark_case_id"],
        execution_mode=config.execution_mode,
        operation=request["operation"],
        started_at=started_at,
        finished_at=finished_at,
        elapsed_ms=elapsed_ms,
        input_sha256=sha256_hex(canonical_json(request)),
        item_count=len(request["context_refs"]),
        question_count=len(request["questions"]),
        screened_clear_count=0,
        escalated_count=0,
        request_count=attempts,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=cost,
        price_usd_per_million_input_tokens=snapshot["price_usd_per_million_input_tokens"],
        price_snapshot_date=snapshot["snapshot_date"],
        resolved_model=resolved_model,
        status=status,
        fallback_used=False,
        error_class=error_class,
    )
    return trace.to_dict()


def execute(request, config, client, clock=None) -> dict:
    validate_request(request)
    if config.execution_mode != "jev_assisted":
        raise ContractError(
            "the Jev runner is only valid on the jev_assisted execution path"
        )
    entry = operation_policy(config.policy, request["operation"])
    if entry["policy_version"] != request["policy_version"]:
        raise ContractError(
            f"request.policy_version {request['policy_version']!r} does not match "
            f"the policy in use ({entry['policy_version']!r})"
        )
    operation_id = operation_id_from_request(request)
    write_atomic(request_path(config.run_dir, operation_id), request)
    write_atomic(pending_path(config.run_dir, operation_id), build_pending_call(request))
    sync_decision_context(
        config, credential_status="unchecked", pending_call=build_pending_call(request)
    )

    def mark_in_flight() -> None:
        pending = build_pending_call(request)
        pending["attempt_status"] = "in_flight"
        write_atomic(pending_path(config.run_dir, operation_id), pending)
        sync_decision_context(
            config, credential_status="available", pending_call=pending
        )

    stopwatch = Stopwatch(clock if clock is not None else time.monotonic)
    started_at = now_iso()
    stopwatch.start()
    outcome = client.call(request, before_dispatch=mark_in_flight)
    stopwatch.stop()
    finished_at = now_iso()
    elapsed_ms = stopwatch.elapsed_ms

    if outcome.status == "succeeded":
        try:
            validate_answer_ids(request, outcome.payload)
        except ContractError as error:
            return _settle(
                request, config, operation_id, "failed",
                error_class="incomplete_response", attempts=outcome.attempts,
                elapsed_ms=elapsed_ms, started_at=started_at, finished_at=finished_at,
            )
        resolved_model = (outcome.payload or {}).get("model")
        if resolved_model != config.policy["pinned_model"]:
            # A different resolved version means the policy's thresholds were
            # never calibrated for the answers that just came back, so this
            # cannot be reported as a succeeded operation.
            return _settle(
                request, config, operation_id, "failed",
                error_class="model_version_mismatch", attempts=outcome.attempts,
                elapsed_ms=elapsed_ms, started_at=started_at, finished_at=finished_at,
            )
        trace = trace_for(
            request, config,
            status="succeeded",
            attempts=outcome.attempts,
            error_class=None,
            resolved_model=resolved_model,
            usage=(outcome.payload or {}).get("usage"),
            elapsed_ms=elapsed_ms,
            started_at=started_at,
            finished_at=finished_at,
        )
        result = build_result(
            request=request, status="succeeded", payload=outcome.payload,
            operation_policy_entry=entry,
            attempts=outcome.attempts, elapsed_ms=elapsed_ms, trace=trace,
            price_usd_per_million_input_tokens=(
                config.policy["price_snapshot"]["price_usd_per_million_input_tokens"]
            ),
        )
        # Terminal state must land before the pending call disappears, so a
        # crash in between leaves a resumable pending call rather than a
        # missing result.
        write_atomic(result_path(config.run_dir, operation_id), result)
        write_atomic(trace_path(config.run_dir, operation_id, outcome.attempts), trace)
        pending_path(config.run_dir, operation_id).unlink(missing_ok=True)
        sync_decision_context(
            config, credential_status="available", pending_call=None,
            resume_cursor=operation_cursor(operation_id),
        )
        return result

    return _settle(
        request, config, operation_id, outcome.status,
        error_class=outcome.error_class, attempts=outcome.attempts,
        elapsed_ms=elapsed_ms, started_at=started_at, finished_at=finished_at,
    )


def _settle(
    request,
    config,
    operation_id,
    status,
    *,
    error_class,
    attempts,
    elapsed_ms,
    started_at,
    finished_at,
):
    pending = build_pending_call(request)
    pending["attempt_status"] = _ATTEMPT_STATUS_FOR_OUTCOME[status]
    trace = trace_for(
        request, config,
        status=status,
        attempts=attempts,
        error_class=error_class,
        resolved_model=None,
        usage=None,
        elapsed_ms=elapsed_ms,
        started_at=started_at,
        finished_at=finished_at,
    )
    result = build_result(
        request=request, status=status, error_class=error_class,
        operation_policy_entry=operation_policy(config.policy, request["operation"]),
        attempts=attempts, elapsed_ms=elapsed_ms, trace=trace,
        price_usd_per_million_input_tokens=(
            config.policy["price_snapshot"]["price_usd_per_million_input_tokens"]
        ),
    )
    write_atomic(pending_path(config.run_dir, operation_id), pending)
    if trace is not None:
        write_atomic(trace_path(config.run_dir, operation_id, attempts), trace)
    sync_decision_context(
        config,
        credential_status=_CREDENTIAL_STATUS_FOR_OUTCOME[status],
        pending_call=pending,
    )
    return result


def run_operation(request, config, client, clock=None, now=None) -> dict:
    # Validate all caller-controlled path components before acquiring a lease
    # or creating any operation directory.
    validate_request(request)
    operation_id = operation_id_from_request(request)
    lease = acquire_lease(config, operation_id, now=now)
    try:
        return execute(request, config, client, clock=clock)
    finally:
        release_lease(config, operation_id, lease.holder)


class NoPendingCall(RuntimeError):
    """There is no recoverable pending call for this run."""


class AmbiguousAttempt(RuntimeError):
    """A previous attempt may have reached the service; consent is required."""


def pending_operation_ids(run_dir: Any) -> list[str]:
    root = Path(run_dir) / "jev"
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and (path / "pending.json").is_file()
    )


def _refs_equal(left: Any, right: Any) -> bool:
    return canonical_json(list(left or [])) == canonical_json(list(right or []))


def resume_operation(
    config: RunnerConfig,
    client,
    current_input_refs,
    *,
    allow_new_attempt: bool = False,
    clock=None,
) -> dict:
    operation_ids = pending_operation_ids(config.run_dir)
    if not operation_ids:
        raise NoPendingCall("this run has no pending Jev call to resume")
    if len(operation_ids) > 1:
        raise NoPendingCall(
            "this run has more than one pending Jev call: " + ", ".join(operation_ids)
        )
    operation_id = operation_ids[0]
    request = read_json(request_path(config.run_dir, operation_id))
    if request is None:
        raise NoPendingCall(f"stored request for {operation_id} is unreadable")
    pending = read_json(pending_path(config.run_dir, operation_id))
    if pending is None:
        raise NoPendingCall(f"pending call for {operation_id} is unreadable")

    if not _refs_equal(current_input_refs, pending.get("input_refs")):
        return _superseded(request, config, operation_id, "input_revisions_changed")
    if request_fingerprint(request) != pending.get("request_fingerprint"):
        return _superseded(request, config, operation_id, "request_fingerprint_mismatch")
    stored_result = read_json(result_path(config.run_dir, operation_id))
    if stored_result is not None:
        # A crash between writing the terminal result and clearing the pending
        # call leaves both files behind. Only return it after confirming that
        # the caller still has the exact revisions and request fingerprint.
        lease = acquire_lease(config, operation_id)
        try:
            pending_path(config.run_dir, operation_id).unlink(missing_ok=True)
            sync_decision_context(
                config, credential_status="available", pending_call=None,
                resume_cursor=operation_cursor(operation_id),
            )
            return stored_result
        finally:
            release_lease(config, operation_id, lease.holder)
    if pending.get("attempt_status") in {"outcome_unknown", "in_flight"}:
        if not allow_new_attempt:
            # The attempt may have been billed, so nothing is re-sent. The
            # operation stays in its own `outcome_unknown` state (not
            # `superseded`, which means "the inputs moved on") and the pending
            # call stays on disk until the user explicitly decides.
            return build_result(
                request=request, status="outcome_unknown",
                operation_policy_entry=operation_policy(
                    config.policy, request["operation"]
                ),
                price_usd_per_million_input_tokens=(
                    config.policy["price_snapshot"]["price_usd_per_million_input_tokens"]
                ),
            )
        raise AmbiguousAttempt(
            "a previous attempt may have been billed; Phase 1 does not create "
            "new attempts automatically"
        )
    lease = acquire_lease(config, operation_id)
    try:
        return execute(request, config, client, clock=clock)
    finally:
        release_lease(config, operation_id, lease.holder)


def _superseded(request, config, operation_id, reason) -> dict:
    pending = build_pending_call(request)
    pending["attempt_status"] = "failed"
    result = build_result(
        request=request, status="failed", error_class="superseded",
        operation_policy_entry=operation_policy(config.policy, request["operation"]),
        attempts=0, elapsed_ms=0,
        price_usd_per_million_input_tokens=(
            config.policy["price_snapshot"]["price_usd_per_million_input_tokens"]
        ),
    )
    result["trace"] = {"superseded_reason": reason}
    validate_result(result)
    write_atomic(pending_path(config.run_dir, operation_id), pending)
    sync_decision_context(
        config, credential_status="available", pending_call=pending,
    )
    return result


CREDENTIAL_ARGUMENT_PREFIXES = (
    "--api-key",
    "--apikey",
    "--api_key",
    "--token",
    "--secret",
    "--authorization",
    "--bearer",
)


def _reject_credential_arguments(arguments) -> str | None:
    """Refuse credential flags before argparse can echo their values."""

    for argument in arguments:
        lowered = str(argument).lower()
        for prefix in CREDENTIAL_ARGUMENT_PREFIXES:
            if lowered.startswith(prefix):
                return (
                    f"credentials are not accepted on the command line: {prefix}. "
                    f"Set {API_KEY_ENV} in the environment instead."
                )
    return None


def _build_parser():
    import argparse

    parser = argparse.ArgumentParser(prog="jev_runner.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="execute one decision operation")
    run.add_argument("--request", required=True)
    run.add_argument("--run-dir", required=True)
    run.add_argument("--policy")

    resume = subparsers.add_parser("resume", help="resume a pending decision operation")
    resume.add_argument("--run-dir", required=True)
    resume.add_argument("--input-refs", required=True)
    resume.add_argument("--policy")
    return parser


def main(
    argv=None,
    environ=None,
    transport_factory=None,
    stdout=None,
    stderr=None,
) -> int:
    import sys

    arguments = list(sys.argv[1:] if argv is None else argv)
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr

    refusal = _reject_credential_arguments(arguments)
    if refusal is not None:
        print(refusal, file=err)
        return 2

    parser = _build_parser()
    if not arguments:
        parser.print_usage(err)
        return 2
    try:
        args = parser.parse_args(arguments)
    except SystemExit as exit_error:
        return 2 if exit_error.code else 0

    factory = transport_factory or UrllibTransport
    try:
        policy = load_policy(args.policy or default_policy_path())
        config = RunnerConfig(run_dir=Path(args.run_dir), policy=policy)
        client = JevClient(factory(), environ=environ)
        if args.command == "run":
            request = json.loads(Path(args.request).read_text(encoding="utf-8"))
            result = run_operation(request, config, client)
        else:
            refs = json.loads(Path(args.input_refs).read_text(encoding="utf-8"))
            result = resume_operation(config, client, refs)
    except (ContractError, JevClientError, NoPendingCall, LeaseHeld, AmbiguousAttempt) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False), file=err)
        return 1
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False), file=err)
        return 1

    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
