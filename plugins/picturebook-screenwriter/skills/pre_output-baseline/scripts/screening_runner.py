"""Orchestrate the text-quality pre-screen over every active red line and page.

The red-line catalog is the recall boundary, not the literal scan: spec §9.2
requires one question per active red line about every relevant page window, and
the scan only contributes candidate evidence. One page window is one item, so a
page's text appears in the request state exactly once while the verdicts stay
per dimension — the same page can be risky on one question and clear on the
others. A window with no text produces no item at all: a wordless spread has
nothing for the model to judge, and a made-up verdict must not be countable as
a cleared item.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_KNOWLEDGE_SCRIPTS = Path(__file__).resolve().parents[2] / "knowledge-loader" / "scripts"
_RUNTIME_SCRIPTS = Path(__file__).resolve().parents[2] / "jev-decision-runtime" / "scripts"
for _path in (_KNOWLEDGE_SCRIPTS, _RUNTIME_SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from decision_contract import (  # noqa: E402
    ContractError,
    operation_policy,
    validate_answer_ids,
    validate_result,
)
from jev_runner import (  # noqa: E402
    HARMLESS_ATTEMPT_STATUSES,
    OPEN_ATTEMPT_STATUSES,
    LeaseHeld,
    VerdictHook,
    discard_failed_pending_call,
    operation_id_from_request,
    pending_path,
    read_json,
    recorded_request_sha256,
    request_path,
    result_answers_request,
    result_path,
    run_operation,
)
from page_quality import dimensions_for, is_last_page, page_facts  # noqa: E402
from routing import UNREADABLE_ANSWER_ERRORS, route_item  # noqa: E402
from screening import (  # noqa: E402
    ScreeningDecision,
    failure_decision,
    screening_decision,
    summarise,
)
from telemetry import benchmark_case_id, request_fingerprint  # noqa: E402

OPERATION = "text_quality_prefilter"
REDLINE_DIMENSION_PREFIX = "redline:"
# The request templates name the item they ask about with this placeholder.
ITEM_PLACEHOLDER = "<page>"
RULE_VERSION = "text-quality-prefilter-rules-v1"
DEFAULT_MAX_ITEMS = 4
# The stored results this screen can route from. `outcome_unknown` is included
# so a terminal record of an ambiguous attempt is never re-sent.
REUSABLE_RESULT_STATUSES = ("succeeded", "outcome_unknown")
# The catalog is rebuilt from the authority pages, not from the bundle's own
# revision numbers, so a request that cites no authority page still has to name
# the rule set it was screened against.
RULESET_REF_ID = "redline_ruleset"
RULESET_REVISION = "text-quality-prefilter-rules-v1"


@dataclass(frozen=True)
class ScreeningOutcome:
    decisions: tuple[ScreeningDecision, ...]
    escalation_package: tuple[dict, ...]
    routes: tuple[dict, ...]
    results: tuple[dict, ...]
    catalog_size: int
    proxy_conflicts: tuple[dict, ...]
    summary: dict
    # The records on disk that kept a batch out of the screen, each with its
    # path and the reason: a run that stops on one of them has to be able to
    # name what stopped it, or the same re-run stops in the same place forever.
    blocked_records: tuple[dict, ...] = ()


@dataclass(frozen=True)
class _StoredBatch:
    """What the run directory already knows about one batch's operation.

    `result` is the terminal result of this exact request when the run has
    already paid for it, and `open_attempt` says that a call for this exact
    request may have reached the service. `blocked_record` and `blocked_reason`
    name the file that made that call, so the report can say which record
    stopped the screen instead of leaving every re-run to stop in the same
    place with nothing to act on.
    """

    result: dict | None = None
    open_attempt: bool = False
    blocked_record: str | None = None
    blocked_reason: str | None = None


def _blocking_record(path: Any, reason: str) -> _StoredBatch:
    """A record that keeps its batch out of the screen, and names itself."""

    return _StoredBatch(
        open_attempt=True, blocked_record=str(path), blocked_reason=reason
    )


def _stored_batch(config, request) -> _StoredBatch:
    """Read what an earlier run of this same batch left behind.

    Every batch is screened once and then reused, for the same two reasons the
    knowledge-relevance screen does it: a run without a credential would
    otherwise pay for the batches it already paid for as soon as the caller
    continues, and the shared runner's `resume_operation` refuses a run that
    holds more than one pending call — so "configure the key, then continue"
    could never be taken. Reuse is bound to the request fingerprint, the same
    identity the shared runner's own resume path uses, so a caller who re-runs
    the screen with new pages or a new catalog is screened again instead of
    being routed on the old evidence.

    Anything on disk this screen cannot route from counts as an attempt that
    may already have been billed, not as an absent record: a request record
    that does not parse cannot be shown to describe the request in hand, a
    terminal record the contract rejects, whose status is not reusable, or
    which was written for other pages is no verdict this batch may be routed
    from, and a pending record whose attempt status is not one that leaves
    nothing open behind it cannot be shown to be free to dispatch. All of them
    keep their batch out of the screen, because sending it again is the one
    mistake the run directory can no longer rule out, and each is named in the
    outcome so the caller can act on the file that stopped the run.

    "Reads as no record" and "exists but cannot be read as one" are different
    answers, so every record that exists is checked for existence as well as
    content: `read_json` returns `None` both for a file that does not parse and
    for one that parses to something other than an object, and treating either
    as an absent record would re-send a batch whose earlier call cannot be
    ruled out — and overwrite the only record of what that call cost.
    """

    operation_id = operation_id_from_request(request)
    stored_request_file = request_path(config.run_dir, operation_id)
    if not stored_request_file.exists():
        # No request was ever recorded for this operation, so nothing can have
        # been billed for it.
        return _StoredBatch()
    stored_request = read_json(stored_request_file)
    if stored_request is None:
        return _blocking_record(stored_request_file, "unreadable_request")
    if request_fingerprint(stored_request) != request_fingerprint(request):
        # The record describes other work: it is neither a result to reuse nor
        # an obstacle to dispatch for these inputs.
        return _StoredBatch()
    stored_result_file = result_path(config.run_dir, operation_id)
    stored_result = read_json(stored_result_file)
    if stored_result is not None:
        if (
            stored_result.get("status") in REUSABLE_RESULT_STATUSES
            and _is_readable_result(stored_result, request)
        ):
            return _StoredBatch(result=stored_result)
        # A terminal record this screen may not route from still sits in this
        # batch's operation directory, so the batch is kept instead of sent
        # again. `result.json` is only rewritten by a dispatch that succeeds
        # while `request.json` is rewritten by every dispatch, so the two ways
        # this happens are told apart by the record's own identity: a result
        # that answers another request is stale, and one that answers this
        # request but fails the contract is unreadable.
        if result_answers_request(stored_result, request):
            reason = "unreadable_result"
        elif recorded_request_sha256(stored_result) is not None:
            reason = "stale_result"
        else:
            # A record that names no request at all: hand-written, or written
            # by a version that did not record one. It is no more readable as a
            # verdict than a file that does not parse.
            reason = "unreadable_result"
        return _blocking_record(stored_result_file, reason)
    if stored_result_file.exists():
        # A file that is there but does not read back as an object: the same
        # shape as an unreadable request record, and the same answer — keep the
        # batch instead of paying for it again.
        return _blocking_record(stored_result_file, "unreadable_result")
    pending_file = pending_path(config.run_dir, operation_id)
    pending = read_json(pending_file)
    if pending is not None:
        attempt_status = pending.get("attempt_status")
        if attempt_status in OPEN_ATTEMPT_STATUSES:
            return _blocking_record(pending_file, "open_attempt")
        if attempt_status in HARMLESS_ATTEMPT_STATUSES:
            # Waiting for a credential, or a call that settled as a failure:
            # neither can have been billed, so dispatching is what retries it.
            return _StoredBatch()
        # A status this build cannot place — an attempt someone else is driving,
        # or a value from a version that knew more statuses — cannot be shown to
        # leave nothing open, so it is treated exactly like a record that does
        # not parse.
        return _blocking_record(pending_file, "unreadable_pending")
    if pending_file.exists():
        return _blocking_record(pending_file, "unreadable_pending")
    return _StoredBatch()


def _is_readable_result(stored, request) -> bool:
    """True when a stored result can be routed from as it stands.

    The reuse path is the only path where a terminal record was not built by
    this process. `run_operation` validates what it just built; a record read
    back from an earlier run, hand-edited, or written by an older version has
    to satisfy the same three things before anything is routed from it: it must
    record that it answered this request (`result_answers_request`, without
    which an edited page would be routed on the version before the edit), the
    result contract, and — for a succeeded record — an answer for every
    question this batch asked, and none for anything else.
    """

    try:
        if not result_answers_request(stored, request):
            return False
        validate_result(stored)
        if stored.get("status") == "succeeded":
            validate_answer_ids(request, stored)
    except (ContractError,) + UNREADABLE_ANSWER_ERRORS:
        return False
    return True


def _unique_records(entries) -> tuple[dict, ...]:
    """The blocked records in the order they were found, each named once."""

    seen: set[tuple] = set()
    unique: list[dict] = []
    for entry in entries:
        key = (entry["path"], entry["reason"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return tuple(unique)


def _settled_reason(result) -> str:
    """Name why a dispatched batch stopped, with its error class when it has one.

    `waiting_for_jev_key` is the runner's own waiting state and carries no error
    class, so it stays exactly that; a settled failure carries the class that
    explains it (`incomplete_response`, `http_500`, `forbidden`, …) and dropping
    it would leave an escalation package whose reason says nothing about what
    the user has to fix.
    """

    error_class = result.get("error_class")
    if error_class:
        return f"{result['status']}:{error_class}"
    return str(result["status"])


def page_windows(pages, width: int = 1) -> tuple[dict, ...]:
    """One window per page, anchored on that page.

    The default window is the page itself. A wider window is the anchor page
    plus the next `width` pages, clipped to the end of the book, because the
    question spec §9.2 asks about a page *run* — "does the negative emotion
    persist with no exit signal?" — cannot be answered from one page. Every page
    still anchors exactly one window, so no page is skipped and none is left
    with an empty window when the configured width exceeds the book.
    """

    pages = tuple(pages)
    windows = []
    for index, page in enumerate(pages):
        window = (page,) if width <= 1 else pages[index:index + width + 1]
        numbers = [str(item.get("no", "")) for item in window]
        # Each page of the window contributes its own `page-<no>` name, so the
        # item id still names every page in a wide window: "page-1-2-3" would
        # lose "page-2" and a reader could not tell which pages were covered.
        windows.append({
            "item_id": "-".join(f"page-{number}" for number in numbers),
            "page_no": numbers[0],
            "kind": "page",
            "text": "\n".join(item.get("text", "") or "" for item in window),
            "page_numbers": numbers,
        })
    return tuple(windows)


def redline_dimensions(rules) -> tuple[str, ...]:
    """One dimension name per active red line.

    Dimensions are generated from the catalog, not from regex matches: the
    literal scan supplies extra evidence, it is not the recall bound.
    """

    return tuple(f"{REDLINE_DIMENSION_PREFIX}{rule.rule_id}" for rule in rules)


def screening_items(pages, rules, window_width: int = 1) -> tuple[dict, ...]:
    """One item per page window, carrying every dimension that applies to it.

    The quality dimensions and the red-line dimensions share a state entry so a
    page's text appears once in the request, while the verdicts stay per
    dimension. The window's first page decides the last-page exemption, which
    only matters when windows are one page wide.
    """

    # Materialise both inputs: a caller may hand over one-shot iterables, and
    # the pages are walked once per window as well as by the last-page rule.
    pages = tuple(pages)
    rules = tuple(rules)
    redline_dims = redline_dimensions(rules)
    patterns = {f"{REDLINE_DIMENSION_PREFIX}{rule.rule_id}": rule.pattern for rule in rules}
    source_keys = tuple(dict.fromkeys(
        rule.source_key for rule in rules if getattr(rule, "source_key", None)
    ))
    items = []
    for window in page_windows(pages, window_width):
        if not window["text"].strip():
            continue
        dimensions = dimensions_for(
            window["page_no"], is_last_page=is_last_page(pages, window["page_no"])
        ) + redline_dims
        items.append({
            "item_id": window["item_id"],
            "page_no": window["page_no"],
            "page_numbers": list(window["page_numbers"]),
            "text": window["text"],
            "facts": page_facts(window["text"]),
            "dimensions": dimensions,
            "redline_patterns": patterns,
            "source_keys": source_keys,
        })
    return tuple(items)


def plan_batches(items, max_items: int) -> tuple[tuple[dict, ...], ...]:
    if max_items < 1:
        raise ValueError("max_items must be at least 1")
    items = tuple(items)
    return tuple(
        items[start:start + max_items] for start in range(0, len(items), max_items)
    )


def _context_refs(batch, bundle) -> list[dict]:
    """Reference the authority pages the red-line catalog came from."""

    revision_vectors = {}
    if bundle is not None:
        for bundle_item in bundle.get("items", ()):
            revision_vectors[str(bundle_item.get("key", ""))] = dict(
                bundle_item.get("source_revisions") or {}
            )
    refs = []
    seen = set()
    for item in batch:
        for key in item.get("source_keys") or ():
            if key in seen or not revision_vectors.get(key):
                continue
            seen.add(key)
            refs.append({
                "ref_id": key, "kind": "knowledge_page",
                "revisions": revision_vectors[key],
            })
    if refs:
        return refs
    # The catalog may be empty or drawn from a page outside this bundle. The
    # request contract needs at least one ref, so name the rule set itself.
    return [{"ref_id": RULESET_REF_ID, "kind": "rule_set",
             "revisions": {"ruleset": RULESET_REVISION}}]


def build_request(
    *,
    run_id: str,
    policy,
    age_band: str,
    batch,
    batch_index: int,
    benchmark_case_id: str,
    bundle: Any = None,
) -> dict:
    """Build one request for a batch of items.

    Every item in the batch shares the state, so each question names its item
    explicitly through the `ITEM_PLACEHOLDER` instead of addressing "the page".
    """

    entry = operation_policy(policy, OPERATION)
    templates = entry["question_templates"]
    pages: dict[str, dict] = {}
    questions: dict[str, dict] = {}

    for item in batch:
        item_id = item["item_id"]
        pages[item_id] = {
            "text": item.get("text", ""),
            "page_numbers": list(item.get("page_numbers") or []),
            **dict(item.get("facts") or {}),
        }
        patterns = item.get("redline_patterns") or {}
        for dimension in item["dimensions"]:
            if dimension.startswith(REDLINE_DIMENSION_PREFIX):
                # The instruction has to name the rule's own literal, so it is
                # built here rather than read from the policy's `redline:*`
                # template. That template stays the routing vocabulary.
                questions[f"{item_id}::{dimension}"] = {
                    "type": "noul",
                    "instructions": (
                        f"针对 `pages.{item_id}.text`：该页窗是否触犯项目红线"
                        f"「{patterns.get(dimension, dimension)}」？"
                    ),
                    "criteria": {"true": "触犯该红线", "false": "不触犯"},
                }
                continue
            template = templates[dimension]
            instructions = str(template["instructions"]).replace(
                ITEM_PLACEHOLDER, item_id
            )
            question = {"type": template["type"], "instructions": instructions}
            if "criteria" in template:
                question["criteria"] = template["criteria"]
            questions[f"{item_id}::{dimension}"] = question

    return {
        "schema_version": "pb-jev-request-v1",
        "run_id": run_id,
        "operation": OPERATION,
        "operation_instance": f"batch-{batch_index:03d}",
        "model": policy["pinned_model"],
        "policy_version": entry["policy_version"],
        "state": {
            "task": {"age_band": age_band, "operation": OPERATION},
            "pages": pages,
        },
        "questions": questions,
        "context_refs": _context_refs(batch, bundle),
        "benchmark_case_id": benchmark_case_id,
    }


def _answers_by_item(result) -> dict[str, dict]:
    """Group flat `<item_id>::<dimension>` answers back into one dict per item."""

    grouped: dict[str, dict] = {}
    for reference, answer in (result.get("answers") or {}).items():
        item_id, separator, dimension = str(reference).partition("::")
        if separator:
            grouped.setdefault(item_id, {})[dimension] = answer
    return grouped


def detect_proxy_conflicts(proxy_findings, decisions) -> tuple[dict, ...]:
    """Report a literal hit that the pre-screen did not escalate.

    spec §9.4 sends a disagreement between the literal scan and the model to the
    plain LLM. A finding names the rule that matched, and that rule's question
    is `<page>::redline:<rule_id>`, so the two signals agree exactly when a
    decision for it was escalated. Nothing else can stand in for it: a cleared
    *page* dimension is not a verdict on this rule, and a rule no window was
    asked about is a coverage gap rather than a cleared item.
    """

    escalated_dimensions = {
        decision.dimension for decision in decisions
        if decision.outcome != "screened_clear"
    }
    return tuple(
        {
            "reason": "proxy_hit_not_escalated",
            "finding_id": finding.id,
            "message": finding.message,
            "evidence": finding.evidence,
        }
        for finding in proxy_findings
        if f"{REDLINE_DIMENSION_PREFIX}{finding.id}" not in escalated_dimensions
    )


def _conflict_escalations(
    conflicts, decisions, text_by_item, facts_by_item, sources_by_item
) -> list[dict]:
    """One escalation entry per literal hit the pre-screen cleared.

    spec §9.4 sends a disagreement between the literal scan and the model to the
    plain LLM rather than letting the two signals pass each other, so a hit may
    not stay a clear verdict somebody could skip once the operation is
    calibrated. The scan reads the whole draft, so it cannot say which page
    holds the literal: every window that was asked about that rule and answered
    clear is part of the disagreement.
    """

    entries: list[dict] = []
    for conflict in conflicts:
        dimension = f"{REDLINE_DIMENSION_PREFIX}{conflict['finding_id']}"
        cleared = [
            decision for decision in decisions
            if decision.dimension == dimension and decision.outcome == "screened_clear"
        ]
        if not cleared:
            # The rule was never asked about, so there is no window to name; the
            # hit still has to reach the LLM, as a draft-scoped entry.
            entries.append({
                "item_id": dimension,
                "dimension": dimension,
                "scope": "draft",
                "outcome": "escalate_llm",
                "reason": "proxy_conflict",
                "evidence": conflict["evidence"],
                "facts": {},
                "probabilities": {},
                "source_keys": [],
                "proxy_finding": dict(conflict),
            })
            continue
        for decision in cleared:
            item_id = decision.item_id.partition("::")[0]
            entries.append({
                "item_id": decision.item_id,
                "dimension": dimension,
                "scope": "window",
                "outcome": "escalate_llm",
                "reason": "proxy_conflict",
                "evidence": text_by_item.get(item_id, ""),
                "facts": facts_by_item.get(item_id, {}),
                "probabilities": dict(decision.probabilities),
                "source_keys": sources_by_item.get(item_id, []),
                "proxy_finding": dict(conflict),
            })
    return entries


def run_screening(
    *,
    run_id: str,
    policy,
    pages,
    rules,
    bundle: Any,
    config,
    client,
    age_band: str = "",
    proxy_findings=(),
    window_width: int = 1,
    clock=None,
) -> ScreeningOutcome:
    """Screen every page dimension and every active red line.

    A verdict is per dimension: every question is addressed
    `<item_id>::<dimension>`, the decisions, the routes and the escalation
    package all use that one name, and one dimension's answer is routed on its
    own so nothing is inferred from a question that item never asked. Only the
    dimensions an item actually carries are iterated, which is what keeps the
    closing page's exempt page-turn question out of the request instead of
    dispatching it with an empty answer set.

    Each batch goes through the shared runner's leased entry point, so a batch
    another runner already holds is never sent twice and a settled failure hands
    its pending record back rather than leaving the run looking like it holds a
    call to resume. A batch whose call did not succeed stops the screen: the
    batches it never reached are recorded as runtime failures with the same
    reason, so a run without a credential does not dispatch one doomed call per
    batch, and no item is ever left without a verdict or cleared by a failure.

    A batch is screened once and then reused: a run directory that already holds
    the verdict for this exact request is routed from, not paid for a second
    time, and a record that may already have been billed — a request whose
    attempt is still open or whose contents cannot be read as this request's
    verdict — stops the screen instead of being sent again. Such a batch is
    reported as a runtime failure and the file that stopped it is named in
    `blocked_records`. `results` holds one entry per batch the screen settled,
    whether this run dispatched it or reused it.
    """

    entry = operation_policy(policy, OPERATION)
    pages = tuple(pages)
    rules = tuple(rules)
    items = screening_items(pages, rules, window_width)
    text_by_item = {item["item_id"]: item.get("text", "") for item in items}
    facts_by_item = {item["item_id"]: dict(item.get("facts") or {}) for item in items}
    sources_by_item = {
        item["item_id"]: list(item.get("source_keys") or ()) for item in items
    }

    cases = benchmark_case_id(
        {"pages": [page.get("text", "") for page in pages],
         "rules": [rule.pattern for rule in rules]},
        entry["policy_version"],
        RULE_VERSION,
    )

    decisions: list[ScreeningDecision] = []
    routes: list[dict] = []
    results: list[dict] = []
    blocked: list[dict] = []
    stopped_reason: str | None = None

    for batch_index, batch in enumerate(
        plan_batches(items, entry.get("max_items_per_request", DEFAULT_MAX_ITEMS)),
        start=1,
    ):
        if stopped_reason is not None:
            _record_batch_failure(decisions, batch, stopped_reason)
            continue
        request = build_request(
            run_id=run_id, policy=policy, age_band=age_band, batch=batch,
            batch_index=batch_index, benchmark_case_id=cases, bundle=bundle,
        )
        hook = None
        stored = _stored_batch(config, request)
        if stored.result is not None:
            # Paid for by an earlier run of this exact batch: route the stored
            # verdict instead of asking the same question twice.
            result = stored.result
        elif stored.open_attempt:
            # A call for this batch may already have been billed, so it is never
            # sent again without the user's explicit consent, and the batch
            # becomes a recorded runtime failure. The screen stops here because
            # the shared runner refuses a run that holds more than one pending
            # call: opening the next batch would keep this run from being
            # continued at all once the record is repaired.
            if stored.blocked_record is not None:
                blocked.append({
                    "operation_id": operation_id_from_request(request),
                    "path": stored.blocked_record,
                    "reason": stored.blocked_reason,
                })
            stopped_reason = str(stored.blocked_reason)
            _record_batch_failure(decisions, batch, stopped_reason)
            continue
        else:
            # The routing runs inside the runner through `VerdictHook`, so this
            # batch's trace records the counts the screen actually decided and
            # the caller reuses that same routing instead of repeating it.
            def compute(_request, payload, batch=batch):
                routed = _route_batch(batch, payload, entry)
                return _verdict_counts(routed[0]), routed

            hook = VerdictHook(compute)
            try:
                result = run_operation(
                    request, config, client, clock=clock, verdicts=hook
                )
            except LeaseHeld:
                stopped_reason = "lease_held"
                _record_batch_failure(decisions, batch, stopped_reason)
                continue
            if result["status"] == "failed":
                # A settled failure is not a call to continue: re-running the
                # screen retries this batch, and the trace stays on disk.
                # Releasing the pending record here is what keeps the next batch
                # (and a later resume) from finding more than one open call.
                discard_failed_pending_call(request, config)
        results.append(result)
        if result["status"] != "succeeded":
            stopped_reason = _settled_reason(result)
            _record_batch_failure(decisions, batch, stopped_reason)
            continue
        routed = hook.value if hook is not None else None
        if routed is None:
            # Reused from an earlier run: this batch's trace was written by the
            # dispatch that paid for it, so the routing here is the caller's
            # own. It is the same function the hook uses.
            routed = _route_batch(batch, result, entry)
        batch_decisions, batch_routes = routed
        decisions.extend(batch_decisions)
        routes.extend(batch_routes)

    conflicts = detect_proxy_conflicts(proxy_findings, decisions)
    conflict_entries = _conflict_escalations(
        conflicts, decisions, text_by_item, facts_by_item, sources_by_item
    )
    escalation_package = [
        {
            "item_id": decision.item_id,
            "dimension": decision.dimension,
            "outcome": decision.outcome,
            "reason": decision.reason or decision.label,
            "evidence": text_by_item.get(decision.item_id.split("::")[0], ""),
            "facts": facts_by_item.get(decision.item_id.split("::")[0], {}),
            "probabilities": dict(decision.probabilities),
            "source_keys": sources_by_item.get(decision.item_id.split("::")[0], []),
        }
        for decision in decisions
        if decision.outcome != "screened_clear"
    ] + conflict_entries

    summary = summarise(decisions)
    summary["catalog_gap"] = len(rules) == 0
    summary["proxy_conflicts"] = len(conflicts)
    summary["escalated_by_proxy_conflict"] = len(conflict_entries)

    return ScreeningOutcome(
        decisions=tuple(decisions),
        escalation_package=tuple(escalation_package),
        routes=tuple(routes),
        results=tuple(results),
        catalog_size=len(rules),
        proxy_conflicts=conflicts,
        summary=summary,
        blocked_records=_unique_records(blocked),
    )


def _record_batch_failure(decisions, batch, reason: str) -> None:
    """Record every dimension of a batch the screen could not dispatch."""

    for item in batch:
        for dimension in item["dimensions"]:
            decisions.append(
                failure_decision(f"{item['item_id']}::{dimension}", dimension, reason)
            )


def _route_batch(batch, result, entry) -> tuple:
    """Route one settled batch into per-dimension decisions and routes.

    One answer per call: the router reads a dimension's answer against the
    questions that item asked, so a red-line answer can never be banded by the
    page-quality rule or the reverse. A dimension left unanswered, and one the
    router cannot read, becomes a recorded runtime failure instead of an
    exception out of the screen — the call behind those answers has already
    been paid for, so a malformed answer may cost its own dimension a route and
    nothing more.

    The same lists supply the trace's verdict counts and the operation's own
    report, so the escalation rate the comparison report shows is the one this
    run actually decided rather than a second computation of the same routing.
    """

    grouped = _answers_by_item(result)
    decisions: list[ScreeningDecision] = []
    routes: list[dict] = []
    for item in batch:
        answers_by_dimension = grouped.get(item["item_id"]) or {}
        for dimension in item["dimensions"]:
            qualified = f"{item['item_id']}::{dimension}"
            answer = answers_by_dimension.get(dimension)
            answers = {dimension: answer} if answer is not None else {}
            if not answers:
                decisions.append(failure_decision(qualified, dimension, "no_answers"))
                continue
            try:
                decision = screening_decision(
                    item_id=qualified, dimension=dimension, answers=answers,
                    operation_policy=entry,
                )
                route = route_item(qualified, answers, entry)
            except UNREADABLE_ANSWER_ERRORS as error:
                decisions.append(
                    failure_decision(qualified, dimension, f"routing_error:{error}")
                )
                continue
            decisions.append(decision)
            if decision.outcome != "runtime_failure":
                routes.append(route)
    return tuple(decisions), tuple(routes)


def _verdict_counts(decisions) -> dict:
    """The counts the shared runner records in this batch's trace.

    `screened_clear_count` is what the pre-screen settled by itself, and
    `escalated_count` is everything it hands to the plain LLM instead — which
    is exactly the escalation package's size. A dimension the screen could not
    decide is as much a review item as a risky one, and the summary's
    escalation ratio counts the two the same way.
    """

    cleared = sum(
        1 for decision in decisions if decision.outcome == "screened_clear"
    )
    return {
        "screened_clear_count": cleared,
        "escalated_count": len(decisions) - cleared,
    }
