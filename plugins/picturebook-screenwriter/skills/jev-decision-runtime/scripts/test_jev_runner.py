import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from decision_contract import (
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
    LeaseHeld,
    NoPendingCall,
    RunnerConfig,
    acquire_lease,
    context_path,
    execute,
    lease_path,
    operation_dir,
    operation_id_for,
    pending_path,
    pending_operation_ids,
    read_decision_context,
    read_json,
    release_lease,
    request_path,
    resume_operation,
    result_path,
    run_operation,
    trace_path,
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


if __name__ == "__main__":
    unittest.main()
