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
from decision_contract import operation_policy  # noqa: E402
from jev_runner import RunnerConfig, execute  # noqa: E402
from recall import partition  # noqa: E402
from required_marking import mark_bundle  # noqa: E402
from routing import UNREADABLE_ANSWER_ERRORS, route_item  # noqa: E402
from telemetry import benchmark_case_id  # noqa: E402

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
    """Screen every recalled soft chunk, keeping everything the model cannot clear."""

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

    for batch_index, batch in enumerate(
        plan_batches(screenable, entry.get("max_items_per_request", 10)), start=1
    ):
        request = build_request(
            run_id=run_id, policy=policy, bundle=bundle,
            artifact_type=artifact_type, task_description=task_description,
            brief=brief, batch=batch, batch_index=batch_index,
            benchmark_case_id=case_id,
        )
        result = execute(request, config, client, clock=clock)
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
