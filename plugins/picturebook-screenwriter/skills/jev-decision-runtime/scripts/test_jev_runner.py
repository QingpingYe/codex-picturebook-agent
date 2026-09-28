import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from decision_contract import (
    ATTEMPT_STATUSES,
    ContractError,
    load_policy,
    default_policy_path,
)
from jev_client import (
    API_KEY_ENV,
    ENDPOINT,
    FakeTransport,
    JevClient,
    JevTransportOutcomeUnknown,
    TransportResponse,
)
from jev_runner import (
    AmbiguousAttempt,
    CREDENTIAL_ARGUMENT_MARKERS,
    HARMLESS_ATTEMPT_STATUSES,
    LeaseHeld,
    NoPendingCall,
    OPEN_ATTEMPT_STATUSES,
    RunnerConfig,
    acquire_lease,
    build_pending_call,
    context_path,
    credential_flag,
    discard_failed_pending_call,
    execute,
    lease_path,
    main,
    operation_dir,
    operation_id_for,
    pending_path,
    pending_operation_ids,
    read_decision_context,
    read_json,
    reject_credential_arguments,
    remaining_pending_call,
    release_lease,
    request_path,
    resume_operation,
    result_path,
    run_operation,
    trace_path,
    verdict_counts,
    write_atomic,
)

RUN_ID = "20260923-example-0001"
MARKER_STATE = "小红帽把蛋糕递给了奶奶"


def make_request(**overrides):
    request = {
        "schema_version": "pb-jev-request-v1",
        "run_id": RUN_ID,
        "operation": "knowledge_relevance",
        "model": "jev-1.13.0",
        "policy_version": "knowledge-relevance-v1",
        "state": MARKER_STATE,
        "questions": {"relevant": {"type": "noul", "instructions": "是否相关？"}},
        "context_refs": [
            {"ref_id": "海外绘本/小老鼠迈尔斯/worldview", "kind": "knowledge_page",
             "revisions": {"node-a": "17"}}
        ],
        "benchmark_case_id": "sha256:" + "0" * 64,
    }
    request.update(overrides)
    return request


def success_body():
    return json.dumps({
        "model": "jev-1.13.0",
        "answers": {"relevant": {"type": "noul", "noul": 0.91}},
        "usage": {"input_tokens": 296, "output_tokens": 20},
    })


# 200 bodies that carry no readable answer envelope: a JSON value that is not
# an object, or an object whose `answers` is not an object keyed by question id.
INVALID_ENVELOPE_BODIES = (
    "[]",
    '"just text"',
    "42",
    "null",
    "true",
    '{"model": "jev-1.13.0", "answers": [[1, 2]]}',
    '{"model": "jev-1.13.0", "answers": 5}',
    '{"model": "jev-1.13.0", "answers": "nope"}',
)


class RunnerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)
        self.config = RunnerConfig(
            run_dir=self.run_dir, policy=load_policy(default_policy_path())
        )
        self.operation_id = operation_id_for(RUN_ID, "knowledge_relevance")

    def tearDown(self):
        self._tmp.cleanup()

    def client(self, responses=None, error=None, environ=None):
        transport = FakeTransport(responses=responses, error=error)
        return JevClient(
            transport,
            environ={API_KEY_ENV: "sk-abc"} if environ is None else environ,
            sleep=lambda _: None,
        ), transport


class AtomicWriteTests(RunnerCase):
    def test_write_atomic_replaces_and_leaves_no_temporary(self):
        target = self.run_dir / "sample.json"
        write_atomic(target, {"a": 1})
        write_atomic(target, {"a": 2})
        self.assertEqual(read_json(target), {"a": 2})
        self.assertEqual(sorted(p.name for p in self.run_dir.iterdir()), ["sample.json"])

    def test_read_json_returns_none_for_missing_or_broken_files(self):
        self.assertIsNone(read_json(self.run_dir / "missing.json"))
        broken = self.run_dir / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        self.assertIsNone(read_json(broken))


class PathLayoutTests(RunnerCase):
    def test_paths_match_the_documented_layout(self):
        self.assertEqual(
            operation_dir(self.run_dir, self.operation_id),
            self.run_dir / "jev" / self.operation_id,
        )
        self.assertEqual(request_path(self.run_dir, self.operation_id).name, "request.json")
        self.assertEqual(pending_path(self.run_dir, self.operation_id).name, "pending.json")
        self.assertEqual(result_path(self.run_dir, self.operation_id).name, "result.json")
        self.assertEqual(
            trace_path(self.run_dir, self.operation_id, 1),
            operation_dir(self.run_dir, self.operation_id) / "trace" / "0001.json",
        )
        self.assertEqual(context_path(self.run_dir).name, "decision-context.json")

    def test_an_operation_instance_gets_its_own_directory(self):
        plain = operation_id_for(RUN_ID, "knowledge_relevance")
        batched = operation_id_for(RUN_ID, "knowledge_relevance", "batch-002")
        self.assertEqual(plain, "20260923-example-0001-knowledge_relevance")
        self.assertEqual(batched, "20260923-example-0001-knowledge_relevance-batch-002")
        self.assertNotEqual(
            request_path(self.run_dir, plain), request_path(self.run_dir, batched)
        )

    def test_operation_id_is_derived_from_the_request_instance(self):
        from jev_runner import operation_id_from_request

        self.assertEqual(operation_id_from_request(make_request()), self.operation_id)
        self.assertEqual(
            operation_id_from_request(make_request(operation_instance="batch-003")),
            f"{self.operation_id}-batch-003",
        )


class SuccessfulRunTests(RunnerCase):
    def test_success_persists_the_result_and_persists_the_request(self):
        client, transport = self.client([TransportResponse(200, success_body(), {})])
        result = run_operation(make_request(), self.config, client)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["resolved_model"], "jev-1.13.0")
        self.assertEqual(result["answers"]["relevant"]["noul"], 0.91)
        self.assertEqual(result["routes"], [])
        self.assertEqual(result["usage"], {"input_tokens": 296, "output_tokens": 20})
        self.assertTrue(request_path(self.run_dir, self.operation_id).is_file())
        self.assertEqual(
            read_json(result_path(self.run_dir, self.operation_id))["status"], "succeeded"
        )
        self.assertEqual(len(transport.calls), 1)

    def test_success_clears_the_pending_call_only_after_the_result_lands(self):
        client, _ = self.client([TransportResponse(200, success_body(), {})])
        run_operation(make_request(), self.config, client)
        self.assertFalse(pending_path(self.run_dir, self.operation_id).exists())
        context = read_decision_context(self.run_dir)
        self.assertIsNone(context["pending_call"])
        self.assertEqual(context["credential_status"], "available")
        self.assertEqual(context["resume_cursor"],
                         f"after_operation:{self.operation_id}")

    def test_a_successful_run_writes_a_trace_without_draft_text(self):
        client, _ = self.client([TransportResponse(200, success_body(), {})])
        run_operation(make_request(), self.config, client)
        trace = read_json(trace_path(self.run_dir, self.operation_id, 1))
        self.assertEqual(trace["schema_version"], "pb-decision-trace-v1")
        self.assertEqual(trace["input_tokens"], 296)
        self.assertEqual(trace["estimated_cost_usd"], "0.000012432000")
        self.assertNotIn(MARKER_STATE, json.dumps(trace, ensure_ascii=False))

    def test_no_written_file_carries_a_credential(self):
        client, _ = self.client([TransportResponse(200, success_body(), {})])
        run_operation(make_request(), self.config, client)
        for path in self.run_dir.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("sk-abc", text)
                self.assertNotIn("Authorization", text)

    def test_only_the_persisted_request_carries_the_state_body(self):
        # Resuming a suspended call needs the request body locally, so the run
        # directory keeps exactly one file holding the input text: request.json.
        # Nothing written for humans or for telemetry may carry it.
        client, _ = self.client([TransportResponse(200, success_body(), {})])
        run_operation(make_request(), self.config, client)
        operation_dir = self.run_dir / "jev" / self.operation_id
        carriers = sorted(
            path.name
            for path in self.run_dir.rglob("*")
            if path.is_file() and MARKER_STATE in path.read_text(encoding="utf-8")
        )
        self.assertEqual(carriers, ["request.json"])
        self.assertEqual(
            read_json(operation_dir / "request.json")["state"], MARKER_STATE
        )

    def test_a_different_resolved_model_is_refused(self):
        # The policy pins the version the thresholds were calibrated against. A
        # response from any other version must not be reported as succeeded, or
        # uncalibrated probabilities would be consumed as if they came from the
        # pinned model.
        body = json.dumps({
            "model": "jev-1.14.0",
            "answers": {"relevant": {"type": "noul", "noul": 0.91}},
            "usage": {"input_tokens": 296, "output_tokens": 20},
        })
        client, _ = self.client([TransportResponse(200, body, {})])
        result = run_operation(make_request(), self.config, client)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_class"], "model_version_mismatch")
        self.assertFalse(result_path(self.run_dir, self.operation_id).exists())
        self.assertTrue(pending_path(self.run_dir, self.operation_id).is_file())


class VerdictCountTests(RunnerCase):
    """The trace carries the operation's own verdict counts, not a pair of zeros.

    The comparison report's escalation rate and every calibration suggestion
    built on it read these two fields, so a runner that always wrote zero made
    the whole Phase 4 report structurally empty.
    """

    def _trace(self):
        return read_json(trace_path(self.run_dir, self.operation_id, 1))

    def test_the_trace_records_the_counts_the_hook_answers_with(self):
        client, _ = self.client([TransportResponse(200, success_body(), {})])
        run_operation(
            make_request(), self.config, client,
            verdicts=lambda request, payload: {
                "screened_clear_count": 3, "escalated_count": 1,
            },
        )
        trace = self._trace()
        self.assertEqual(trace["screened_clear_count"], 3)
        self.assertEqual(trace["escalated_count"], 1)

    def test_a_run_without_a_hook_records_the_zero_pair(self):
        client, _ = self.client([TransportResponse(200, success_body(), {})])
        run_operation(make_request(), self.config, client)
        trace = self._trace()
        self.assertEqual(trace["screened_clear_count"], 0)
        self.assertEqual(trace["escalated_count"], 0)

    def test_a_hook_that_raises_never_loses_the_paid_call(self):
        # Routing an answer the provider has already billed for may not cost
        # the run its terminal result. The counts fall back to the zero pair,
        # which reads downstream as "nothing here to calibrate from" rather
        # than as "this batch was clear".
        client, _ = self.client([TransportResponse(200, success_body(), {})])

        def broken(request, payload):
            raise RuntimeError("routing exploded")

        result = run_operation(make_request(), self.config, client, verdicts=broken)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(self._trace()["escalated_count"], 0)
        self.assertEqual(self._trace()["screened_clear_count"], 0)
        self.assertFalse(pending_path(self.run_dir, self.operation_id).exists())

    def test_a_failed_attempt_records_no_verdicts_and_calls_no_hook(self):
        # A batch that did not settle decided nothing, so it may not claim a
        # single cleared or escalated verdict.
        body = json.dumps({"model": "jev-1.13.0", "answers": {}, "usage": {}})
        client, _ = self.client([TransportResponse(200, body, {})])
        asked = []
        result = run_operation(
            make_request(), self.config, client,
            verdicts=lambda request, payload: asked.append(1) or {
                "screened_clear_count": 9, "escalated_count": 9,
            },
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(asked, [])
        self.assertEqual(self._trace()["screened_clear_count"], 0)
        self.assertEqual(self._trace()["escalated_count"], 0)

    def test_a_hook_that_answers_with_nonsense_records_the_zero_pair(self):
        for counts in (
            None, "3", 7, {"escalated_count": 1},
            {"screened_clear_count": -1, "escalated_count": 0},
            {"screened_clear_count": True, "escalated_count": 2},
        ):
            with self.subTest(counts=counts):
                self.assertEqual(
                    verdict_counts(lambda *_: counts, {}, {}), (0, 0)
                )

    def test_a_hook_that_answers_with_a_pair_is_read_as_a_pair(self):
        self.assertEqual(
            verdict_counts(
                lambda *_: {"screened_clear_count": 0, "escalated_count": 4},
                {}, {},
            ),
            (0, 4),
        )


class IncompleteResponseTests(RunnerCase):
    def test_a_missing_answer_id_fails_without_writing_a_result(self):
        body = json.dumps({"model": "jev-1.13.0", "answers": {}, "usage": {}})
        client, _ = self.client([TransportResponse(200, body, {})])
        result = run_operation(make_request(), self.config, client)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_class"], "incomplete_response")
        self.assertFalse(result_path(self.run_dir, self.operation_id).exists())
        self.assertTrue(pending_path(self.run_dir, self.operation_id).is_file())

    def test_a_failed_attempt_is_recorded_as_failed_not_cleared(self):
        client, _ = self.client([TransportResponse(422, "bad field", {})])
        result = run_operation(make_request(), self.config, client)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            read_json(pending_path(self.run_dir, self.operation_id))["attempt_status"],
            "failed",
        )

    def test_an_answer_value_that_cannot_be_read_settles_instead_of_raising(self):
        # The answer carries the right question id, so it passes answer-id
        # validation, and its value is unreadable rather than misplaced: the
        # refusal comes from the result contract, which is only reached after
        # routing has already tried to band that value. The call is paid for, so
        # the operation has to settle with a terminal failure instead of raising
        # out of the runner and leaving a pending call with no result.
        item = "海外绘本/小老鼠迈尔斯/worldview#003"
        request = make_request(questions={
            f"{item}::relevant": {"type": "noul", "instructions": "x"},
        })
        body = json.dumps({
            "model": "jev-1.13.0",
            "answers": {f"{item}::relevant": {"type": "noul", "noul": "很相关"}},
            "usage": {"input_tokens": 296, "output_tokens": 20},
        })
        client, transport = self.client([TransportResponse(200, body, {})])
        result = run_operation(request, self.config, client)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_class"], "incomplete_response")
        self.assertEqual(len(transport.calls), 1)
        self.assertFalse(result_path(self.run_dir, self.operation_id).exists())
        self.assertTrue(pending_path(self.run_dir, self.operation_id).is_file())

    def test_a_malformed_envelope_settles_instead_of_raising(self):
        # Every shape a provider can return inside a 200 body without a usable
        # envelope: the body is a JSON value but not an object, or it is an
        # object whose `answers` is not an object of question ids. The call is
        # already paid for when the runner reads it, so each of these has to
        # settle the operation with a terminal failure instead of raising out of
        # the runner and leaving a pending call with no outcome.
        for body in INVALID_ENVELOPE_BODIES:
            with self.subTest(body=body):
                client, transport = self.client([TransportResponse(200, body, {})])
                result = run_operation(make_request(), self.config, client)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error_class"], "incomplete_response")
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(
                    read_json(
                        pending_path(self.run_dir, self.operation_id)
                    )["attempt_status"],
                    "failed",
                )

    def test_result_construction_refuses_an_envelope_it_cannot_read(self):
        # Routing runs inside the result construction, so the envelope has to be
        # checked before any band is computed: an answer set that is not an
        # object must be a contract failure, never an `AttributeError`/`TypeError`
        # raised while routing tries to read a band out of it.
        from decision_contract import operation_policy
        from jev_runner import build_result

        entry = operation_policy(
            load_policy(default_policy_path()), "knowledge_relevance"
        )
        payloads = ([], 5, {"answers": [[1, 2]]}, {"answers": 5})
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ContractError):
                    build_result(
                        request=make_request(), status="succeeded",
                        payload=payload, operation_policy_entry=entry,
                    )


class WaitingRunTests(RunnerCase):
    def test_a_missing_key_waits_and_writes_no_result(self):
        client, transport = self.client(environ={})
        result = run_operation(make_request(), self.config, client)
        self.assertEqual(result["status"], "waiting_for_jev_key")
        self.assertEqual(result["answers"], {})
        self.assertEqual(result["routes"], [])
        self.assertEqual(result["trace"], {})
        self.assertIsNone(result["resolved_model"])
        self.assertEqual(transport.calls, [])
        self.assertFalse(result_path(self.run_dir, self.operation_id).exists())
        context = read_decision_context(self.run_dir)
        self.assertEqual(context["credential_status"], "waiting_for_jev_key")
        self.assertIsNotNone(context["pending_call"])

    def test_waiting_keeps_the_pending_call_at_pending(self):
        client, _ = self.client(environ={})
        run_operation(make_request(), self.config, client)
        pending = read_json(pending_path(self.run_dir, self.operation_id))
        self.assertEqual(pending["attempt_status"], "pending")

    def test_a_waiting_run_writes_no_trace(self):
        client, _ = self.client(environ={})
        run_operation(make_request(), self.config, client)
        self.assertFalse(trace_path(self.run_dir, self.operation_id, 1).exists())

    def test_forbidden_has_its_own_waiting_state(self):
        client, _ = self.client([TransportResponse(403, "", {})])
        result = run_operation(make_request(), self.config, client)
        self.assertEqual(result["status"], "waiting_for_jev_access")
        self.assertEqual(
            read_decision_context(self.run_dir)["credential_status"],
            "waiting_for_jev_access",
        )


class RequestGateTests(RunnerCase):
    def test_a_policy_version_mismatch_is_refused_before_dispatch(self):
        client, transport = self.client([TransportResponse(200, success_body(), {})])
        with self.assertRaises(ContractError):
            run_operation(
                make_request(policy_version="knowledge-relevance-v99"),
                self.config, client,
            )
        self.assertEqual(transport.calls, [])

    def test_an_invalid_request_is_refused_before_dispatch(self):
        client, transport = self.client([TransportResponse(200, success_body(), {})])
        with self.assertRaises(ContractError):
            run_operation(make_request(questions={}), self.config, client)
        self.assertEqual(transport.calls, [])

    def test_an_llm_mode_run_is_refused_here(self):
        client, transport = self.client([TransportResponse(200, success_body(), {})])
        config = RunnerConfig(run_dir=self.run_dir,
                              policy=self.config.policy, execution_mode="llm")
        with self.assertRaises(ContractError):
            run_operation(make_request(), config, client)
        self.assertEqual(transport.calls, [])

    def test_invalid_run_id_is_rejected_before_any_lease_path_is_created(self):
        escaped_name = "escaped-" + self.run_dir.name
        request = make_request(run_id=f"../../{escaped_name}")
        escaped_operation_dir = self.run_dir.parent.parent / f"{escaped_name}-knowledge_relevance"
        client, transport = self.client([TransportResponse(200, success_body(), {})])
        with self.assertRaises(ContractError):
            run_operation(request, self.config, client)
        self.assertFalse(escaped_operation_dir.exists())
        self.assertEqual(transport.calls, [])


class ResumeTests(RunnerCase):
    def _wait_for_key(self):
        client, transport = self.client(environ={})
        result = run_operation(make_request(), self.config, client)
        self.assertEqual(result["status"], "waiting_for_jev_key")
        return transport

    def test_pending_operation_ids_lists_the_waiting_operation(self):
        self._wait_for_key()
        self.assertEqual(pending_operation_ids(self.run_dir), [self.operation_id])

    def test_resume_continues_the_same_operation_after_the_key_appears(self):
        self._wait_for_key()
        client, transport = self.client([TransportResponse(200, success_body(), {})])
        result = resume_operation(
            self.config, client, make_request()["context_refs"]
        )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(len(transport.calls), 1)
        self.assertFalse(pending_path(self.run_dir, self.operation_id).exists())

    def test_resume_does_not_re_load_knowledge_or_ask_again(self):
        self._wait_for_key()
        client, _ = self.client([TransportResponse(200, success_body(), {})])
        resume_operation(self.config, client, make_request()["context_refs"])
        context = read_decision_context(self.run_dir)
        self.assertEqual(context["mode"], "jev_assisted")
        self.assertEqual(context["selection_status"], "confirmed")

    def test_changed_input_revisions_supersede_the_old_pending_call(self):
        self._wait_for_key()
        client, transport = self.client([TransportResponse(200, success_body(), {})])
        moved = [{"ref_id": "海外绘本/小老鼠迈尔斯/worldview", "kind": "knowledge_page",
                  "revisions": {"node-a": "18"}}]
        result = resume_operation(self.config, client, moved)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_class"], "superseded")
        self.assertEqual(transport.calls, [])
        self.assertFalse(result_path(self.run_dir, self.operation_id).exists())

    def test_resume_without_a_pending_call_is_refused(self):
        client, _ = self.client([TransportResponse(200, success_body(), {})])
        with self.assertRaises(NoPendingCall):
            resume_operation(self.config, client, make_request()["context_refs"])

    def test_resume_finishes_the_bookkeeping_when_the_result_already_landed(self):
        # A crash between writing result.json and clearing pending.json leaves
        # both on disk. The work is already done and billed, so resuming must
        # return the recorded result instead of sending a second request.
        client, _ = self.client([TransportResponse(200, success_body(), {})])
        run_operation(make_request(), self.config, client)
        stored = read_json(result_path(self.run_dir, self.operation_id))
        write_atomic(
            pending_path(self.run_dir, self.operation_id),
            build_pending_call(make_request()),
        )
        resume_client, transport = self.client(
            [TransportResponse(200, success_body(), {})]
        )
        result = resume_operation(
            self.config, resume_client, make_request()["context_refs"]
        )
        self.assertEqual(transport.calls, [])
        self.assertEqual(result, stored)
        self.assertFalse(pending_path(self.run_dir, self.operation_id).exists())
        context = read_decision_context(self.run_dir)
        self.assertIsNone(context["pending_call"])
        self.assertEqual(context["resume_cursor"], f"after_operation:{self.operation_id}")

    def test_resume_checks_freshness_before_returning_a_stored_result(self):
        # A terminal result belongs to the revisions in its stored request.
        # Even in the crash window, a caller with newer revisions must not get
        # the old successful result as if it described current knowledge.
        client, _ = self.client([TransportResponse(200, success_body(), {})])
        run_operation(make_request(), self.config, client)
        write_atomic(
            pending_path(self.run_dir, self.operation_id),
            build_pending_call(make_request()),
        )
        resume_client, transport = self.client(
            [TransportResponse(200, success_body(), {})]
        )
        moved = [{"ref_id": "海外绘本/小老鼠迈尔斯/worldview", "kind": "knowledge_page",
                  "revisions": {"node-a": "99"}}]
        result = resume_operation(self.config, resume_client, moved)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_class"], "superseded")
        self.assertEqual(transport.calls, [])

    def test_a_stored_result_that_answers_another_request_is_not_returned(self):
        # `result.json` is only rewritten by a dispatch that succeeds while
        # `request.json` is rewritten by every dispatch, so a request whose own
        # attempt is still open can sit beside an older request's verdict. That
        # verdict answers a different question, so resuming has to fall back to
        # what the pending record says instead of handing it back — and it must
        # not delete that record, which is the only thing that says a call may
        # already have been billed.
        client, _ = self.client([TransportResponse(200, success_body(), {})])
        run_operation(make_request(), self.config, client)

        other = make_request(state="被改写过的 state")
        write_atomic(request_path(self.run_dir, self.operation_id), other)
        pending = build_pending_call(other)
        pending["attempt_status"] = "outcome_unknown"
        write_atomic(pending_path(self.run_dir, self.operation_id), pending)

        resume_client, transport = self.client(
            [TransportResponse(200, success_body(), {})]
        )
        result = resume_operation(self.config, resume_client, other["context_refs"])
        self.assertEqual(result["status"], "outcome_unknown")
        self.assertEqual(transport.calls, [])
        self.assertTrue(pending_path(self.run_dir, self.operation_id).is_file())

    def test_a_corrupt_stored_request_is_not_replayed(self):
        # A crash can leave a truncated request.json behind. Replaying it would
        # re-send a request nobody can read, so an unreadable file counts as
        # "nothing to resume" and the caller has to rebuild the operation.
        self._wait_for_key()
        request_path(self.run_dir, self.operation_id).write_text(
            "{not json", encoding="utf-8"
        )
        client, transport = self.client([TransportResponse(200, success_body(), {})])
        with self.assertRaises(NoPendingCall):
            resume_operation(self.config, client, make_request()["context_refs"])
        self.assertEqual(transport.calls, [])

    def test_two_pending_operations_cannot_be_resumed_by_guess(self):
        # decision_context.pending_call has a single slot while the run
        # directory can hold several waiting operations, so the context always
        # describes the most recent one. Resume must enumerate the disk and
        # refuse, never guess which operation the user meant.
        client, _ = self.client(environ={})
        run_operation(make_request(), self.config, client)
        run_operation(
            make_request(operation_instance="batch-002"), self.config, client
        )
        self.assertEqual(
            pending_operation_ids(self.run_dir),
            [self.operation_id, f"{self.operation_id}-batch-002"],
        )
        context = read_decision_context(self.run_dir)
        self.assertEqual(
            context["pending_call"]["operation_id"],
            f"{self.operation_id}-batch-002",
        )
        resume_client, transport = self.client(
            [TransportResponse(200, success_body(), {})]
        )
        with self.assertRaises(NoPendingCall):
            resume_operation(
                self.config, resume_client, make_request()["context_refs"]
            )
        self.assertEqual(transport.calls, [])

    def test_switching_to_llm_dispatches_nothing_and_keeps_the_pending_call(self):
        # "Switch back to the plain LLM" must not be read as "this waiting call
        # is resolved": no request goes out, and the pending call stays visible
        # until the user deals with it.
        self._wait_for_key()
        llm_config = RunnerConfig(
            run_dir=self.config.run_dir,
            policy=self.config.policy,
            execution_mode="llm",
        )
        client, transport = self.client([TransportResponse(200, success_body(), {})])
        with self.assertRaises(ContractError):
            execute(make_request(), llm_config, client)
        self.assertEqual(transport.calls, [])
        self.assertTrue(pending_path(self.run_dir, self.operation_id).is_file())

    def test_a_tampered_request_fingerprint_is_refused(self):
        self._wait_for_key()
        stored = read_json(request_path(self.run_dir, self.operation_id))
        stored["state"] = "被改写过的 state"
        write_atomic(request_path(self.run_dir, self.operation_id), stored)
        client, transport = self.client([TransportResponse(200, success_body(), {})])
        result = resume_operation(self.config, client, make_request()["context_refs"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_class"], "superseded")
        self.assertEqual(transport.calls, [])

    def test_an_ambiguous_attempt_needs_explicit_consent_to_resend(self):
        client, transport = self.client(error=JevTransportOutcomeUnknown("read timed out"))
        first = run_operation(make_request(), self.config, client)
        self.assertEqual(first["status"], "outcome_unknown")

        retry_client, retry_transport = self.client(
            [TransportResponse(200, success_body(), {})]
        )
        refused = resume_operation(
            self.config, retry_client, make_request()["context_refs"]
        )
        self.assertEqual(refused["status"], "outcome_unknown")
        self.assertEqual(retry_transport.calls, [])
        with self.assertRaises(AmbiguousAttempt):
            resume_operation(
                self.config, retry_client, make_request()["context_refs"],
                allow_new_attempt=True,
            )
        self.assertEqual(retry_transport.calls, [])

    def test_a_crash_left_in_flight_call_is_not_automatically_replayed(self):
        self._wait_for_key()
        pending = read_json(pending_path(self.run_dir, self.operation_id))
        pending["attempt_status"] = "in_flight"
        write_atomic(pending_path(self.run_dir, self.operation_id), pending)
        client, transport = self.client([TransportResponse(200, success_body(), {})])
        result = resume_operation(self.config, client, make_request()["context_refs"])
        self.assertEqual(result["status"], "outcome_unknown")
        self.assertEqual(transport.calls, [])

    def test_in_flight_is_persisted_before_transport_dispatch(self):
        class InspectingTransport:
            def __init__(inner_self):
                inner_self.pending_status = None

            def send(inner_self, url, headers, body, timeout):
                pending = read_json(pending_path(self.run_dir, self.operation_id))
                inner_self.pending_status = pending["attempt_status"]
                return TransportResponse(200, success_body(), {})

        transport = InspectingTransport()
        client = JevClient(
            transport, environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None
        )
        result = run_operation(make_request(), self.config, client)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(transport.pending_status, "in_flight")


class LeaseTests(RunnerCase):
    def test_acquire_then_release_frees_the_operation(self):
        lease = acquire_lease(self.config, self.operation_id)
        self.assertEqual(lease.holder, "primary")
        release_lease(self.config, self.operation_id, "primary")
        acquire_lease(self.config, self.operation_id)

    def test_a_second_holder_is_refused_while_the_lease_is_active(self):
        acquire_lease(self.config, self.operation_id)
        with self.assertRaises(LeaseHeld):
            acquire_lease(self.config, self.operation_id)

    def test_an_expired_lease_can_be_taken_over(self):
        start = datetime(2026, 9, 23, 10, 30, tzinfo=timezone.utc)
        acquire_lease(
            self.config, self.operation_id,
            now=start,
        )
        later = start + timedelta(minutes=self.config.lease_ttl_minutes + 1)
        lease = acquire_lease(self.config, self.operation_id, now=later)
        self.assertEqual(lease.holder, "primary")

    def test_release_by_another_holder_is_refused(self):
        acquire_lease(self.config, self.operation_id)
        with self.assertRaises(LeaseHeld):
            release_lease(self.config, self.operation_id, "someone-else")

    def test_the_lease_is_released_after_a_successful_run(self):
        client, _ = self.client([TransportResponse(200, success_body(), {})])
        run_operation(make_request(), self.config, client)
        self.assertFalse(lease_path(self.run_dir, self.operation_id).exists())

    def test_the_lease_is_released_after_a_waiting_run(self):
        client, _ = self.client(environ={})
        run_operation(make_request(), self.config, client)
        self.assertFalse(lease_path(self.run_dir, self.operation_id).exists())

    def test_a_run_in_progress_blocks_a_second_runner(self):
        acquire_lease(self.config, self.operation_id)
        client, transport = self.client([TransportResponse(200, success_body(), {})])
        with self.assertRaises(LeaseHeld):
            run_operation(make_request(), self.config, client)
        self.assertEqual(transport.calls, [])


class OutcomeUnknownTests(RunnerCase):
    def test_an_unknown_outcome_keeps_the_pending_call_resumable(self):
        client, _ = self.client(error=JevTransportOutcomeUnknown("read timed out"))
        result = run_operation(make_request(), self.config, client)
        self.assertEqual(result["status"], "outcome_unknown")
        pending = read_json(pending_path(self.run_dir, self.operation_id))
        self.assertEqual(pending["attempt_status"], "outcome_unknown")

    def test_an_unknown_outcome_writes_a_trace_but_no_result(self):
        client, _ = self.client(error=JevTransportOutcomeUnknown("read timed out"))
        run_operation(make_request(), self.config, client)
        self.assertTrue(trace_path(self.run_dir, self.operation_id, 1).is_file())
        self.assertFalse(result_path(self.run_dir, self.operation_id).exists())

    def test_the_lease_is_released_after_an_ambiguous_outcome(self):
        client, _ = self.client(error=JevTransportOutcomeUnknown("read timed out"))
        run_operation(make_request(), self.config, client)
        self.assertFalse(lease_path(self.run_dir, self.operation_id).exists())


class DiscardFailedPendingCallTests(RunnerCase):
    """The screening loop's failure cleanup, under the operation's lease.

    One call per batch means a settled failure has to give its pending record
    back, but the deletion itself is a write another runner's call must not
    lose: only the record of this request's own settled failure goes.
    """

    def _failed_pending(self):
        client, _ = self.client([TransportResponse(500, "boom", {})])
        result = run_operation(make_request(), self.config, client)
        self.assertEqual(result["status"], "failed")
        path = pending_path(self.run_dir, self.operation_id)
        self.assertTrue(path.is_file())
        return path

    def _rewrite_pending(self, path, **fields):
        record = read_json(path)
        record.update(fields)
        write_atomic(path, record)

    def test_a_settled_failure_gives_its_pending_record_back(self):
        path = self._failed_pending()
        self.assertTrue(discard_failed_pending_call(make_request(), self.config))
        self.assertFalse(path.is_file())
        self.assertEqual(pending_operation_ids(self.run_dir), [])

    def test_an_attempt_that_may_have_been_billed_is_left_alone(self):
        path = self._failed_pending()
        self._rewrite_pending(path, attempt_status="in_flight")
        self.assertFalse(discard_failed_pending_call(make_request(), self.config))
        self.assertTrue(path.is_file())

    def test_a_record_for_other_knowledge_is_left_alone(self):
        path = self._failed_pending()
        self._rewrite_pending(path, request_fingerprint="sha256:" + "f" * 64)
        self.assertFalse(discard_failed_pending_call(make_request(), self.config))
        self.assertTrue(path.is_file())

    def test_a_record_behind_a_held_lease_is_left_alone(self):
        path = self._failed_pending()
        other = RunnerConfig(
            run_dir=self.run_dir, policy=self.config.policy, holder="another-runner"
        )
        acquire_lease(other, self.operation_id)
        self.assertFalse(discard_failed_pending_call(make_request(), self.config))
        self.assertTrue(path.is_file())


class RunLevelPendingCallTests(RunnerCase):
    """`decision-context.json` has one `pending_call` slot for the whole run."""

    def _waiting_operation(self, instance=""):
        """Leave one operation on disk waiting for the credential."""

        client, _ = self.client(environ={})
        result = run_operation(
            make_request(operation_instance=instance) if instance else make_request(),
            self.config,
            client,
        )
        self.assertEqual(result["status"], "waiting_for_jev_key")
        return f"{self.operation_id}-{instance}" if instance else self.operation_id

    def test_every_attempt_status_is_either_open_or_harmless(self):
        # The screening path dispatches a batch again only for the statuses
        # classified as harmless, and keeps it for the open ones; anything the
        # two tuples do not cover has to fall on the conservative side. Pinning
        # the union against the contract's own vocabulary is what keeps a status
        # added to `ATTEMPT_STATUSES` from being read as "safe to send".
        self.assertEqual(
            set(HARMLESS_ATTEMPT_STATUSES) | set(OPEN_ATTEMPT_STATUSES),
            set(ATTEMPT_STATUSES),
        )
        self.assertEqual(
            set(HARMLESS_ATTEMPT_STATUSES) & set(OPEN_ATTEMPT_STATUSES), set()
        )

    def test_a_successful_write_keeps_another_operations_open_call(self):
        other_id = self._waiting_operation(instance="batch-002")
        client, _ = self.client([TransportResponse(200, success_body(), {})])
        result = run_operation(make_request(), self.config, client)
        self.assertEqual(result["status"], "succeeded")

        self.assertEqual(pending_operation_ids(self.run_dir), [other_id])
        context = read_decision_context(self.run_dir)
        self.assertEqual(context["pending_call"]["operation_id"], other_id)
        self.assertEqual(context["pending_call"]["attempt_status"], "pending")

    def test_a_discarded_failure_keeps_another_operations_open_call(self):
        client, _ = self.client([TransportResponse(500, "boom", {})])
        run_operation(make_request(), self.config, client)
        other_id = self._waiting_operation(instance="batch-002")

        self.assertTrue(discard_failed_pending_call(make_request(), self.config))
        self.assertEqual(pending_operation_ids(self.run_dir), [other_id])
        context = read_decision_context(self.run_dir)
        self.assertEqual(context["pending_call"]["operation_id"], other_id)

    # `resume_operation` refuses a run that holds more than one pending record
    # before it reaches its own terminal-write branch, so its use of the same
    # helper is a guard against a record written in that window rather than a
    # state a test can put the runner into. The two branches below are the
    # reachable ones.
    def test_the_slot_is_cleared_when_nothing_else_is_waiting(self):
        # The other half of the rule: the context still stops claiming a pending
        # call once the run really has none.
        self._waiting_operation()
        client, _ = self.client([TransportResponse(200, success_body(), {})])
        self.assertIsNone(remaining_pending_call(self.config, self.operation_id))
        run_operation(make_request(), self.config, client)
        self.assertEqual(pending_operation_ids(self.run_dir), [])
        self.assertIsNone(read_decision_context(self.run_dir)["pending_call"])


class CliTests(RunnerCase):
    def _write(self, name, payload):
        path = self.run_dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def _main(self, argv, client=None, transport=None, **kwargs):
        stdout = io.StringIO()
        stderr = io.StringIO()
        if client is None:
            client, transport = self.client([TransportResponse(200, success_body(), {})])
        code = main(
            argv, environ={API_KEY_ENV: "sk-abc"}, transport_factory=lambda: transport,
            stdout=stdout, stderr=stderr, **kwargs,
        )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_run_prints_the_result_json(self):
        path = self._write("request.json", make_request())
        code, out, _ = self._main(["run", "--request", str(path), "--run-dir", str(self.run_dir)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "succeeded")

    def test_resume_prints_the_result_json(self):
        client, _ = self.client(environ={})
        run_operation(make_request(), self.config, client)
        refs = self._write("refs.json", make_request()["context_refs"])
        code, out, _ = self._main(
            ["resume", "--run-dir", str(self.run_dir), "--input-refs", str(refs)]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "succeeded")

    def test_credential_arguments_are_refused_without_echoing_the_value(self):
        for marker in CREDENTIAL_ARGUMENT_MARKERS:
            with self.subTest(marker=marker):
                stdout = io.StringIO()
                stderr = io.StringIO()
                code = main(
                    ["run", f"--{marker}=SUPER-SECRET-VALUE",
                     "--run-dir", str(self.run_dir)],
                    environ={}, stdout=stdout, stderr=stderr,
                )
                self.assertEqual(code, 2)
                self.assertNotIn("SUPER-SECRET-VALUE", stderr.getvalue())
                self.assertNotIn("SUPER-SECRET-VALUE", stdout.getvalue())

    def test_a_provider_prefixed_credential_flag_is_refused_too(self):
        # `--openai-api-key=…` is the same mistake as `--api-key=…`. argparse
        # prints the value it was handed to the process's own stderr before any
        # of these CLIs can refuse the request, so the gate has to catch the
        # shape of the flag rather than a fixed list of spellings.
        for flag in ("--openai-api-key", "--OPENAI_API_KEY", "--anthropic-api-key",
                     "--access-key", "--secret_key", "--client-secret",
                     "--my-token", "--password", "--db-credential"):
            with self.subTest(flag=flag):
                refusal = reject_credential_arguments([f"{flag}=SUPER-SECRET-VALUE"])
                self.assertIsNotNone(refusal)
                self.assertNotIn("SUPER-SECRET-VALUE", refusal)

    def test_a_vendor_prefixed_key_flag_is_refused_by_its_shape(self):
        # The provider list can never be complete, so a trailing `key` segment
        # is refused whatever stands in front of it: `--openai-key`,
        # `--typesafe-key` and `--my-key` are all the same mistake.
        for flag in ("--key", "--openai-key", "--anthropic-key", "--typesafe-key",
                     "--my-key", "--vendor-key", "--OPENAI_KEY"):
            with self.subTest(flag=flag):
                refusal = reject_credential_arguments([f"{flag}=SUPER-SECRET-VALUE"])
                self.assertIsNotNone(refusal)
                self.assertNotIn("SUPER-SECRET-VALUE", refusal)

    def test_a_key_that_names_an_ordering_or_a_word_is_not_a_credential(self):
        # `--sort-key` and `--primary-key` name a column, not a secret, and
        # `--monkey` names an animal. Refusing those would make the gate refuse
        # the ordinary arguments of unrelated tools that share a command line.
        for argument in ("--sort-key=2", "--SORT_KEY=2", "--primary-key=id",
                         "--foreign-key=user_id", "--cache-key=page-1",
                    "--monkey=1", "--hotkey=ctrl+k", "--shortcut-key=x"):
            with self.subTest(argument=argument):
                self.assertIsNone(credential_flag(argument))
                self.assertIsNone(reject_credential_arguments([argument]))

    def test_a_key_with_its_qualifier_glued_on_is_refused_too(self):
        # Providers write the same argument both ways, and a rule that only
        # reads the last `-`/`_` segment lets the glued spelling through to
        # argparse, which echoes whatever it is handed — the leak this gate
        # exists to close.
        for flag in ("--mykey", "--userkey", "--authkey", "--typesafekey",
                     "--nameKey", "--apikey", "--OPENAI_KEY"):
            with self.subTest(flag=flag):
                refusal = reject_credential_arguments([f"{flag}=SUPER-SECRET-VALUE"])
                self.assertIsNotNone(refusal)
                self.assertNotIn("SUPER-SECRET-VALUE", refusal)

    def test_a_flag_that_counts_tokens_is_not_a_credential(self):
        # A count of tokens is not the token itself: the gate judges whole
        # `-`/`_`-separated segments, so `--max-tokens` is an ordinary argument
        # rather than a credential to refuse.
        for argument in ("--max-tokens=5", "--input-tokens=5",
                         "--tokens-per-page=5", "--max-tokens"):
            with self.subTest(argument=argument):
                self.assertIsNone(credential_flag(argument))
                self.assertIsNone(reject_credential_arguments([argument]))

    def test_an_ordinary_flag_or_value_is_not_read_as_a_credential(self):
        # The gate may not refuse these CLIs' own arguments, and it judges the
        # flag name only: a path that happens to contain the word "key" is data
        # the caller is allowed to pass.
        for argument in ("--run-dir", "--input-refs", "--request", "--policy",
                         "--script", "--window-width", "--escalation-out",
                         "--run-dir=C:/tmp/keys", "scripts/keys.json"):
            with self.subTest(argument=argument):
                self.assertIsNone(credential_flag(argument))
                self.assertIsNone(reject_credential_arguments([argument]))

    def test_a_missing_policy_file_is_reported_as_a_contract_failure(self):
        path = self._write("request.json", make_request())
        code, _, err = self._main(
            ["run", "--request", str(path), "--run-dir", str(self.run_dir),
             "--policy", str(self.run_dir / "nope.json")]
        )
        self.assertEqual(code, 1)
        self.assertIn("error", err.lower())

    def test_usage_errors_exit_two(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = main([], environ={}, stdout=stdout, stderr=stderr)
        self.assertEqual(code, 2)


class RoutePopulationTests(RunnerCase):
    """`routes` is per item: the answers are grouped and the rules applied."""

    ITEM = "海外绘本/小老鼠迈尔斯/worldview#003"
    CLEAR_ANSWERS = {
        f"{ITEM}::relevant": {"type": "noul", "noul": 0.05},
        f"{ITEM}::usable_evidence": {"type": "noul", "noul": 0.05},
        f"{ITEM}::contradicts_task_assumption": {"type": "noul", "noul": 0.05},
        f"{ITEM}::instruction_like_content": {"type": "noul", "noul": 0.05},
    }
    ITEM_QUESTIONS = {
        f"{ITEM}::relevant": {"type": "noul", "instructions": "x"},
        f"{ITEM}::usable_evidence": {"type": "noul", "instructions": "x"},
        f"{ITEM}::contradicts_task_assumption": {"type": "noul", "instructions": "x"},
        f"{ITEM}::instruction_like_content": {"type": "noul", "instructions": "x"},
    }

    def test_a_successful_result_routes_each_item(self):
        body = json.dumps({
            "model": "jev-1.13.0",
            "answers": self.CLEAR_ANSWERS,
            "usage": {"input_tokens": 100, "output_tokens": 10},
        })
        client, _ = self.client([TransportResponse(200, body, {})])
        request = make_request()
        request["questions"] = self.ITEM_QUESTIONS
        policy = load_policy(default_policy_path())
        policy["operations"]["knowledge_relevance"]["routing"] = {
            "bands": {"clear_at_or_below": "0.25", "risk_at_or_above": "0.70"},
            "rules": [{"route": "exclude_soft", "label": "clearly_irrelevant",
                       "all_of": [{"question_id": "relevant", "bands": ["clear"]}]}],
        }
        policy["operations"]["knowledge_relevance"]["question_templates"] = {
            "relevant": {"type": "noul", "instructions": "x"},
            "usable_evidence": {"type": "noul", "instructions": "x"},
            "contradicts_task_assumption": {"type": "noul", "instructions": "x"},
            "instruction_like_content": {"type": "noul", "instructions": "x"},
        }
        config = RunnerConfig(run_dir=self.run_dir, policy=policy)
        result = run_operation(request, config, client)
        self.assertEqual(result["routes"], [{
            "item_id": self.ITEM,
            "route": "exclude_soft",
            "label": "clearly_irrelevant",
        }])

    def test_a_failed_result_carries_no_routes(self):
        client, _ = self.client([TransportResponse(200, json.dumps(
            {"model": "jev-1.13.0", "answers": {}, "usage": {}}), {})])
        result = run_operation(make_request(), self.config, client)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["routes"], [])

    def test_an_answer_key_without_an_item_separator_is_refused(self):
        from jev_runner import _answers_by_item
        from decision_contract import ContractError

        with self.assertRaises(ContractError):
            _answers_by_item({"relevant": {"type": "noul", "noul": 0.5}})

    def test_an_unbandable_answer_leaves_its_item_unrouted(self):
        # A choice answer to a noul question is a well-formed answer payload, so
        # `validate_result` accepts it; it just cannot be placed in a band. The
        # provider call is already paid for, so this has to settle as a terminal
        # result rather than raise out of the runner, and the item that cannot be
        # banded has to stay unrouted while its neighbour still routes normally.
        other = self.ITEM.replace("#003", "#004")
        body = json.dumps({
            "model": "jev-1.13.0",
            "answers": {
                f"{self.ITEM}::relevant": {
                    "type": "choice", "choice": "yes",
                    "probabilities": {"yes": 1.0}, "confidence": 1.0,
                },
                f"{other}::relevant": {"type": "noul", "noul": 0.05},
            },
            "usage": {"input_tokens": 10, "output_tokens": 1},
        })
        client, _ = self.client([TransportResponse(200, body, {})])
        request = make_request(questions={
            f"{self.ITEM}::relevant": {"type": "noul", "instructions": "x"},
            f"{other}::relevant": {"type": "noul", "instructions": "x"},
        })
        result = run_operation(request, self.config, client)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual([route["item_id"] for route in result["routes"]], [other])
        self.assertEqual(
            read_json(result_path(self.run_dir, self.operation_id))["status"],
            "succeeded",
        )

    def test_a_flat_question_id_set_still_settles_without_routes(self):
        # Phase 1 shaped requests name questions without an item. They cannot be
        # routed, and an unrouted item needs review, but the operation has to
        # finish: aborting here would throw away a call that has already been
        # paid for and leave a pending call with no terminal result.
        client, _ = self.client([TransportResponse(200, success_body(), {})])
        result = run_operation(make_request(), self.config, client)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["routes"], [])
        self.assertTrue(result_path(self.run_dir, self.operation_id).is_file())

    def test_an_unreadable_answer_value_costs_only_its_own_item_its_route(self):
        # A noul answer whose value is missing, or is not a number, cannot be
        # placed in a band. Reading it must not raise out of the result
        # construction: the provider call is already paid for, so the answer
        # costs its own item a route and leaves its neighbour's route alone.
        from decision_contract import operation_policy
        from jev_runner import _routes_for

        missing_value = self.ITEM
        non_numeric_value = self.ITEM.replace("#003", "#004")
        readable = self.ITEM.replace("#003", "#005")
        entry = operation_policy(
            load_policy(default_policy_path()), "knowledge_relevance"
        )
        routes = _routes_for(
            "succeeded",
            {
                f"{missing_value}::relevant": {"type": "noul"},
                f"{non_numeric_value}::relevant": {"type": "noul", "noul": "很相关"},
                f"{readable}::relevant": {"type": "noul", "noul": 0.05},
            },
            entry,
        )
        self.assertEqual([route["item_id"] for route in routes], [readable])


if __name__ == "__main__":
    unittest.main()
