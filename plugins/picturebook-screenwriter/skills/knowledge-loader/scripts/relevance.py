"""The knowledge_relevance operation: screen authority chunks before the model sees them."""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The screening step runs on the shared decision runtime, so its scripts join
# the import path here: the module composes both skills' modules.
RUNTIME_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "jev-decision-runtime" / "scripts"
)
if str(RUNTIME_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SCRIPTS))

from chunker import Chunk, evidence_field  # noqa: E402
from decision_contract import (  # noqa: E402
    ContractError,
    operation_policy,
    validate_answer_ids,
    validate_result,
)
from jev_runner import (  # noqa: E402
    RunnerConfig,
    operation_id_from_request,
    pending_path,
    read_json,
    request_path,
    result_path,
    run_operation,
    sync_decision_context,
)
from recall import partition  # noqa: E402
from required_marking import mark_bundle  # noqa: E402
from routing import UNREADABLE_ANSWER_ERRORS, route_item  # noqa: E402
from telemetry import benchmark_case_id, request_fingerprint  # noqa: E402

OPERATION = "knowledge_relevance"
QUESTION_ORDER = (
    "relevant",
    "usable_evidence",
    "contradicts_task_assumption",
    "instruction_like_content",
)

# Jev documents 64k total context with `state + longest single question` capped
# at 32k tokens, and CJK text costs roughly one token per character, so the
# character budget stays well under the token budget. This is a transport
# guard, not a decision threshold: nothing here routes or excludes anything.
MAX_STATE_CHARS = 24000

# The rules the screening result is recorded against, so a benchmark case
# changes when the bands or the rule order change.
RULE_VERSION = "knowledge-relevance-rules-v1"

# The request templates name the item they ask about with this placeholder.
ITEM_PLACEHOLDER = "<item>"

# The outcomes that leave an open call on disk instead of a decision: the caller
# still has to configure a credential, or the attempt's fate is unknown.
OPEN_ATTEMPT_STATUSES = ("outcome_unknown", "in_flight")

# A batch whose call is still open (waiting for a credential) or that may
# already have been billed stops the screen where it is: the remaining batches
# are left unscreened rather than opened behind the caller's back.
BATCH_STOP_OUTCOMES = (
    "waiting_for_jev_key",
    "waiting_for_jev_access",
    "outcome_unknown",
)

# The stored results this screen can route from. `outcome_unknown` is included
# so a terminal record of an ambiguous attempt is never re-sent: it settles its
# batch as kept and uncertain instead.
REUSABLE_RESULT_STATUSES = ("succeeded", "outcome_unknown")


class ContextBudgetError(ValueError):
    """A batch cannot fit the provider's state-plus-question budget.

    An over-budget batch is a caller-fixable gap, and every runner CLI in this
    plugin reports `ValueError` as a JSON error, so this derives from
    `ValueError`: the reported gap lands on that documented path instead of
    escaping the handler as a traceback.
    """


@dataclass(frozen=True)
class RelevanceOutcome:
    kept: tuple[Chunk, ...]
    always_kept: tuple[Chunk, ...]
    conflicts: tuple[Chunk, ...]
    uncertain: tuple[Chunk, ...]
    excluded_soft: tuple[dict, ...]
    routes: tuple[dict, ...]
    results: tuple[dict, ...]
    required_count: int
    candidate_count: int


def _items(bundle: Any) -> tuple:
    if isinstance(bundle, Mapping):
        return tuple(bundle.get("items", ()))
    return tuple(getattr(bundle, "items", ()))


def benchmark_input(bundle: Any, artifact_type: str) -> dict:
    """The normalized input two runs must share to be compared."""

    return {
        "artifact_type": artifact_type,
        "pages": [
            {
                "key": evidence_field(item, "key", ""),
                "revision_id": evidence_field(item, "revision_id", 0),
                "source_revisions": dict(
                    evidence_field(item, "source_revisions", {}) or {}
                ),
            }
            for item in _items(bundle)
        ],
    }


def plan_batches(
    candidates: Sequence[Chunk], max_items: int
) -> tuple[tuple[Chunk, ...], ...]:
    if max_items < 1:
        raise ValueError("max_items must be at least 1")
    candidates = tuple(candidates)
    return tuple(
        candidates[start:start + max_items]
        for start in range(0, len(candidates), max_items)
    )


def _refs_for(bundle: Any, batch: Sequence[Chunk]) -> list[dict]:
    """One context ref per page in the batch, carrying its version vector."""

    revision_vectors = {
        str(evidence_field(item, "key", "")): dict(
            evidence_field(item, "source_revisions", {}) or {}
        )
        for item in _items(bundle)
    }
    refs: dict[str, dict] = {}
    for chunk in batch:
        refs.setdefault(chunk.key, {
            "ref_id": chunk.key,
            "kind": "knowledge_page",
            "revisions": revision_vectors.get(chunk.key, {}),
        })
    return list(refs.values())


def build_request(
    *,
    run_id: str,
    policy: Mapping[str, Any],
    bundle: Any,
    artifact_type: str,
    task_description: str,
    brief: str,
    batch: Sequence[Chunk],
    batch_index: int,
    benchmark_case_id: str,
) -> dict:
    """Build one `pb-jev-request-v1` for a batch of candidate chunks."""

    entry = operation_policy(policy, OPERATION)
    templates = entry["question_templates"]
    chunks = {
        chunk.chunk_id: {"heading": " / ".join(chunk.heading_path), "text": chunk.text}
        for chunk in batch
    }
    questions: dict[str, dict] = {}
    for chunk in batch:
        for question_id in QUESTION_ORDER:
            template = templates[question_id]
            instructions = str(template["instructions"]).replace(
                ITEM_PLACEHOLDER, chunk.chunk_id
            )
            question = {"type": template["type"], "instructions": instructions}
            if "criteria" in template:
                question["criteria"] = template["criteria"]
            questions[f"{chunk.chunk_id}::{question_id}"] = question
    state = {
        "task": {
            "artifact_type": artifact_type,
            "stage": task_description,
            "brief": brief,
        },
        "chunks": chunks,
    }
    _assert_state_budget(state, questions)
    return {
        "schema_version": "pb-jev-request-v1",
        "run_id": run_id,
        "operation": OPERATION,
        "operation_instance": f"batch-{batch_index:03d}",
        "model": policy["pinned_model"],
        "policy_version": entry["policy_version"],
        "state": state,
        "questions": questions,
        "context_refs": _refs_for(bundle, batch),
        "benchmark_case_id": benchmark_case_id,
    }


def _assert_state_budget(
    state: Mapping[str, Any], questions: Mapping[str, Any]
) -> None:
    """Refuse a batch whose state cannot fit the provider's context budget.

    Required chunks are never dropped to make room, so an over-budget batch is
    a gap for the caller to resolve rather than something to trim silently: it
    must not be sent as a request that can only come back 422 after the upload
    path has already been paid for.
    """

    used = len(json.dumps(state, ensure_ascii=False, sort_keys=True))
    longest = max(
        (
            len(json.dumps(question, ensure_ascii=False, sort_keys=True))
            for question in questions.values()
        ),
        default=0,
    )
    if used + longest > MAX_STATE_CHARS:
        raise ContextBudgetError(
            f"state plus longest question would be {used + longest} characters, "
            f"over the {MAX_STATE_CHARS} character budget"
        )


def _probability(answer: Any) -> float | None:
    """The bandable probability of one answer, or None when it carries none.

    Only noul answers hold a probability, and a choice or score answer to a
    noul question is still a contract-valid response, so an exclusion record
    written after the paid call reads what it can instead of raising.
    """

    if not isinstance(answer, Mapping) or answer.get("type") != "noul":
        return None
    return float(answer["noul"])


def _excluded_entry(
    chunk: Chunk, answers: Mapping[str, Any], route: Mapping[str, Any]
) -> dict:
    probabilities = {}
    for question_id in QUESTION_ORDER:
        if question_id not in answers:
            continue
        value = _probability(answers[question_id])
        if value is not None:
            probabilities[question_id] = value
    return {
        "chunk_id": chunk.chunk_id,
        "key": chunk.key,
        "route": route["route"],
        "label": route.get("label"),
        "probabilities": probabilities,
    }


def _route_chunk(
    chunk: Chunk, answers: Mapping[str, Any] | None, entry: Mapping[str, Any]
) -> dict | None:
    """Route one chunk, or return None when its answers cannot be read.

    The batch has already been paid for by the time a chunk is routed, so a
    missing or malformed answer set degrades that single item to kept and
    uncertain instead of aborting the operation with no terminal result. The
    tolerated errors are the shared `UNREADABLE_ANSWER_ERRORS`: the same list
    the result construction uses, so both layers agree on what "cannot be read"
    means.
    """

    if not answers:
        return None
    try:
        return route_item(chunk.chunk_id, answers, entry)
    except UNREADABLE_ANSWER_ERRORS:
        return None


def _answers_by_item(result: Mapping[str, Any]) -> dict[str, dict]:
    """Group one result's flat `<item_id>::<question_id>` answers by item."""

    grouped: dict[str, dict] = {}
    for question_ref, answer in (result.get("answers") or {}).items():
        item_id, separator, question_id = str(question_ref).partition("::")
        if separator:
            grouped.setdefault(item_id, {})[question_id] = answer
    return grouped


@dataclass(frozen=True)
class _StoredBatch:
    """What the run directory already knows about one batch's operation.

    `result` is the terminal result of this exact request when the run has
    already paid for it, and `open_attempt` says that a call for this exact
    request may have reached the service.
    """

    result: dict | None = None
    open_attempt: bool = False


def _stored_batch(config: RunnerConfig, request: Mapping[str, Any]) -> _StoredBatch:
    """Read what an earlier run of this same batch left behind.

    Every batch is screened once and then reused: a bundle that needs more than
    one request would otherwise open a call per batch, and the shared runner's
    `resume_operation` refuses a run that holds more than one pending call — so
    the documented "configure the key, then continue" step could never be
    taken. Reuse is bound to the request fingerprint, the same identity the
    shared runner's own resume path uses, so a caller who re-runs the screen
    with new knowledge is screened again instead of being routed on the old
    evidence.

    Anything on disk this screen cannot route from counts as an attempt that
    may already have been billed, not as an absent record: a request record
    that does not parse cannot be shown to describe the request in hand, and a
    terminal record the contract rejects is no verdict this batch may be
    routed from. Both keep their batch out of the screen, because sending it
    again is the one mistake the run directory can no longer rule out.
    """

    operation_id = operation_id_from_request(request)
    stored_request_file = request_path(config.run_dir, operation_id)
    if not stored_request_file.exists():
        # No request was ever recorded for this operation, so nothing can have
        # been billed for it.
        return _StoredBatch()
    stored_request = read_json(stored_request_file)
    if stored_request is None:
        return _StoredBatch(open_attempt=True)
    if request_fingerprint(stored_request) != request_fingerprint(request):
        # The record describes other knowledge: it is neither a result to
        # reuse nor an obstacle to dispatch for these inputs.
        return _StoredBatch()
    stored_result = read_json(result_path(config.run_dir, operation_id))
    if stored_result is not None:
        if (
            stored_result.get("status") in REUSABLE_RESULT_STATUSES
            and _is_readable_result(stored_result, request)
        ):
            return _StoredBatch(result=stored_result)
        # A terminal record this screen may not route from — one the contract
        # rejects, or one no status makes reusable — still says a call was
        # settled here, so its batch is kept instead of sent again.
        return _StoredBatch(open_attempt=True)
    pending = read_json(pending_path(config.run_dir, operation_id))
    if pending is not None:
        return _StoredBatch(
            open_attempt=pending.get("attempt_status") in OPEN_ATTEMPT_STATUSES
        )
    # A pending record that exists but cannot be read cannot rule out a billed
    # attempt either; a missing one means no attempt was recorded for these
    # inputs, so the batch is safe to dispatch.
    return _StoredBatch(
        open_attempt=pending_path(config.run_dir, operation_id).exists()
    )


def _is_readable_result(stored: Any, request: Mapping[str, Any]) -> bool:
    """True when a stored result can be routed from as it stands.

    The reuse path is the only path where a terminal record was not built by
    this process. `execute` validates what it just built; a record read back
    from an earlier run, hand-edited, or written by an older version has to
    satisfy the same two things before anything is routed from it: the result
    contract, and — for a succeeded record — an answer for every question this
    batch asked, and none for anything else. A record whose answer ids name no
    question of this request cannot be read as a verdict on it, so its batch is
    kept rather than routed from it.
    """

    try:
        validate_result(stored)
        if stored.get("status") == "succeeded":
            validate_answer_ids(request, stored)
    except (ContractError,) + UNREADABLE_ANSWER_ERRORS:
        return False
    return True


def screen_candidates(
    *,
    run_id: str,
    policy: Mapping[str, Any],
    artifact_type: str,
    task_description: str,
    brief: str,
    bundle: Any,
    config: RunnerConfig,
    client: Any,
    declared_page_types: Sequence[str] = (),
    declared_keys: Sequence[str] = (),
    terms: Sequence[str] = (),
    clock: Any = None,
) -> RelevanceOutcome:
    """Screen every recalled soft chunk, keeping everything the model cannot clear.

    A bundle that needs more than one request is screened one batch at a time.
    A batch whose call is still open (waiting for a credential, or an attempt
    whose fate is unknown) stops the screen there: the batches it never reached
    stay kept and are reported uncertain, so nothing is ever excluded on
    evidence that was not collected. Re-running the screen once the credential
    is configured picks up from that point, reusing each batch that already
    settled instead of paying for it twice.

    Every dispatch takes the shared runner's per-operation execution lease, so
    a batch another runner holds is not sent either. A batch that settles as a
    failure is retried by re-running the screen rather than through `resume`,
    which leaves at most one call for the caller to continue with.
    """

    marked = mark_bundle(
        bundle, declared_page_types=declared_page_types, declared_keys=declared_keys
    )
    always_kept, candidates = partition(
        marked, artifact_type=artifact_type, terms=tuple(terms)
    )
    # `chunk_id` is `<page key>#<ordinal>`, so two pages that share a key share
    # every question id. The request questions and the state chunks are keyed by
    # that id, so one of the two texts would answer for both chunks and a page
    # would be routed on another page's evidence. Only an id that names exactly
    # one chunk can be screened; a collision is kept and reported instead.
    repeated = Counter(chunk.chunk_id for chunk in candidates)
    screenable = tuple(
        chunk for chunk in candidates if repeated[chunk.chunk_id] == 1
    )
    unscreenable = tuple(
        chunk for chunk in candidates if repeated[chunk.chunk_id] > 1
    )
    entry = operation_policy(policy, OPERATION)
    case_id = benchmark_case_id(
        benchmark_input(bundle, artifact_type),
        entry["policy_version"],
        RULE_VERSION,
    )

    kept: list[Chunk] = list(always_kept)
    conflicts: list[Chunk] = []
    uncertain: list[Chunk] = []
    excluded: list[dict] = []
    routes: list[dict] = []
    results: list[dict] = []

    for chunk in unscreenable:
        kept.append(chunk)
        uncertain.append(chunk)

    stopped = False
    for batch_index, batch in enumerate(
        plan_batches(screenable, entry.get("max_items_per_request", 10)), start=1
    ):
        if stopped:
            # An earlier batch holds an open call, so this one was never
            # dispatched: like every chunk without a verdict it stays kept and
            # is reported uncertain, and nothing is excluded.
            kept.extend(batch)
            uncertain.extend(batch)
            continue
        request = build_request(
            run_id=run_id, policy=policy, bundle=bundle,
            artifact_type=artifact_type, task_description=task_description,
            brief=brief, batch=batch, batch_index=batch_index,
            benchmark_case_id=case_id,
        )
        stored = _stored_batch(config, request)
        if stored.result is not None:
            # Paid for in an earlier run: reuse it rather than dispatch again.
            result = stored.result
        elif stored.open_attempt:
            # The attempt may already have been billed, so nothing is re-sent
            # without the user's explicit consent. The batch stays kept and
            # uncertain, and the rest of the bundle stays unscreened.
            kept.extend(batch)
            uncertain.extend(batch)
            stopped = True
            continue
        else:
            # Dispatch through the shared runner's leased entry point, the same
            # one the single-operation path uses: another runner holding this
            # operation's lease must stop this batch from being sent at all.
            result = run_operation(request, config, client, clock=clock)
            if result["status"] == "failed":
                # A settled failure is not a call to continue. The runner keeps
                # the pending record so one failed operation stays resumable,
                # but a screen holds one call per batch and `resume_operation`
                # refuses a run that holds more than one: leaving this record
                # behind would make a batch that only waits for the credential
                # later on unresumable. Re-running the screen retries a failed
                # batch instead, and its result and trace stay on disk.
                pending_path(
                    config.run_dir, operation_id_from_request(request)
                ).unlink(missing_ok=True)
                sync_decision_context(
                    config, credential_status="available", pending_call=None
                )
        results.append(result)
        answers_by_item = (
            _answers_by_item(result) if result["status"] == "succeeded" else {}
        )

        for chunk in batch:
            answers = answers_by_item.get(chunk.chunk_id)
            route = _route_chunk(chunk, answers, entry)
            if route is None:
                # No verdict at all, or one that cannot be read as a verdict:
                # the chunk is kept and flagged, never dropped.
                kept.append(chunk)
                uncertain.append(chunk)
                continue
            routes.append(route)
            if route["route"] == "exclude_soft":
                excluded.append(_excluded_entry(chunk, answers, route))
                continue
            kept.append(chunk)
            if route["route"] == "escalate_llm":
                conflicts.append(chunk)
                continue
            if route.get("label") == "uncertain" or route["route"] == "needs_user_choice":
                uncertain.append(chunk)

        if result["status"] in BATCH_STOP_OUTCOMES:
            # This batch's call is still open, so no further batch is opened in
            # this run: the caller configures the credential and continues,
            # and every batch is dispatched exactly once along the way.
            stopped = True

    return RelevanceOutcome(
        kept=tuple(kept),
        always_kept=tuple(always_kept),
        conflicts=tuple(conflicts),
        uncertain=tuple(uncertain),
        excluded_soft=tuple(excluded),
        routes=tuple(routes),
        results=tuple(results),
        required_count=sum(1 for chunk in marked if chunk.required),
        candidate_count=len(candidates),
    )


def dependency_bundle(bundle: Any) -> dict:
    """Return the bundle used for the `built_against` lock.

    The lock record must describe the knowledge the artifact was actually built
    against, including pages whose chunks were all excluded. Feeding it the
    filtered bundle would silently drop those pages from staleness detection.
    """

    if isinstance(bundle, Mapping):
        return dict(bundle)
    from load_knowledge import bundle_to_dict

    return bundle_to_dict(bundle)


def filtered_bundle(bundle: Any, outcome: RelevanceOutcome) -> dict:
    """Return the evidence bundle to put in the model context.

    Every surviving page keeps its full version vector; only its `content` is
    reduced to the kept chunks. Pages with nothing left are dropped from the
    context bundle but remain in `dependency_bundle`.
    """

    payload = dependency_bundle(bundle)
    kept_by_key: dict[str, list[Chunk]] = {}
    for chunk in outcome.kept:
        kept_by_key.setdefault(chunk.key, []).append(chunk)
    items = []
    for item in payload.get("items", ()):
        chunks = kept_by_key.get(str(item.get("key", "")))
        if not chunks:
            continue
        reduced = dict(item)
        reduced["content"] = "\n\n".join(chunk.text for chunk in chunks)
        items.append(reduced)
    filtered = dict(payload)
    filtered["items"] = items
    warnings = list(payload.get("warnings", ()))
    if outcome.excluded_soft:
        warnings.append(
            f"本次有 {len(outcome.excluded_soft)} 个软知识块未进入模型上下文；"
            "它们仍在权威知识库中，只是与当前产物无关。"
        )
    if outcome.uncertain:
        warnings.append(
            f"本次有 {len(outcome.uncertain)} 个软知识块无法判定相关性，已保守保留。"
        )
    filtered["warnings"] = warnings
    return filtered
