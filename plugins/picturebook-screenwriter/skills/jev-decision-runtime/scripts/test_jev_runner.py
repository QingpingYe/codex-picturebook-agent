import json
import tempfile
import unittest
from pathlib import Path

from decision_contract import (
    ContractError,
    load_policy,
    default_policy_path,
)
from jev_client import API_KEY_ENV, ENDPOINT, FakeTransport, JevClient, TransportResponse
from jev_runner import (
    RunnerConfig,
    context_path,
    operation_dir,
    operation_id_for,
    pending_path,
    read_decision_context,
    read_json,
    request_path,
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


if __name__ == "__main__":
    unittest.main()
