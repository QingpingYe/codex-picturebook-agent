"""Phase 1 integration: both execution paths, credential recovery, redaction."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
RUNTIME_SCRIPTS = ROOT / "skills" / "jev-decision-runtime" / "scripts"
sys.path.insert(0, str(RUNTIME_SCRIPTS))

from decision_contract import load_policy, default_policy_path  # noqa: E402
from jev_client import (  # noqa: E402
    API_KEY_ENV,
    FakeTransport,
    JevClient,
    JevEndpointError,
    TransportResponse,
    UrllibTransport,
)
from jev_runner import (  # noqa: E402
    RunnerConfig,
    build_decision_context_for_run,
    operation_id_for,
    pending_path,
    read_decision_context,
    read_json,
    result_path,
    resume_operation,
    run_operation,
    sync_decision_context,
    trace_path,
    write_atomic,
)

RUN_ID = "20260923-integration-0001"
OPERATION = "knowledge_relevance"
MARKER_STATE = "小红帽把蛋糕递给了奶奶，奶奶说谢谢你"
API_KEY = "sk-integration-secret"


def make_request():
    return {
        "schema_version": "pb-jev-request-v1",
        "run_id": RUN_ID,
        "operation": OPERATION,
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


def success_body():
    return json.dumps({
        "model": "jev-1.13.0",
        "answers": {"relevant": {"type": "noul", "noul": 0.91}},
        "usage": {"input_tokens": 296, "output_tokens": 20},
    })


class Phase1IntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)
        self.config = RunnerConfig(
            run_dir=self.run_dir, policy=load_policy(default_policy_path())
        )
        self.operation_id = operation_id_for(RUN_ID, OPERATION)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_plain_llm_path_never_touches_the_runtime(self):
        context = build_decision_context_for_run(
            mode="llm", external_text_processing_acknowledged=False,
            selected_at="2026-09-23T10:30:00+08:00",
        )
        write_atomic(self.run_dir / "decision-context.json", context)
        self.assertFalse((self.run_dir / "jev").exists())
        self.assertFalse((self.run_dir / "decision-context.json").read_text(
            encoding="utf-8"
        ).count(API_KEY_ENV))

    def test_missing_key_then_local_key_then_resume(self):
        transport = FakeTransport(responses=[TransportResponse(200, success_body(), {})])

        waiting = run_operation(
            make_request(), self.config,
            JevClient(transport, environ={}, sleep=lambda _: None),
        )
        self.assertEqual(waiting["status"], "waiting_for_jev_key")
        self.assertEqual(transport.calls, [])
        context = read_decision_context(self.run_dir)
        self.assertEqual(context["credential_status"], "waiting_for_jev_key")
        self.assertIsNotNone(context["pending_call"])
        self.assertTrue(
            context["pending_call"]["request_fingerprint"].startswith("sha256:")
        )
        self.assertEqual(context["mode"], "jev_assisted")

        resumed = resume_operation(
            self.config,
            JevClient(transport, environ={API_KEY_ENV: API_KEY}, sleep=lambda _: None),
            make_request()["context_refs"],
        )
        self.assertEqual(resumed["status"], "succeeded")
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(
            read_json(result_path(self.run_dir, self.operation_id))["status"], "succeeded"
        )
        self.assertFalse(pending_path(self.run_dir, self.operation_id).exists())

    def test_recovery_does_not_repeat_completed_stages(self):
        transport = FakeTransport(responses=[TransportResponse(200, success_body(), {})])
        run_operation(make_request(), self.config,
                      JevClient(transport, environ={}, sleep=lambda _: None))
        operation_dir = self.run_dir / "jev" / self.operation_id
        request_before = read_json(operation_dir / "request.json")
        refs_digest_before = json.dumps(request_before["context_refs"], sort_keys=True)

        resume_operation(
            self.config,
            JevClient(transport, environ={API_KEY_ENV: API_KEY}, sleep=lambda _: None),
            make_request()["context_refs"],
        )
        request_after = read_json(operation_dir / "request.json")
        self.assertEqual(
            json.dumps(request_after["context_refs"], sort_keys=True), refs_digest_before
        )

    def test_changed_knowledge_revision_does_not_replay_the_old_request(self):
        transport = FakeTransport(responses=[TransportResponse(200, success_body(), {})])
        run_operation(make_request(), self.config,
                      JevClient(transport, environ={}, sleep=lambda _: None))
        moved = [{"ref_id": "海外绘本/小老鼠迈尔斯/worldview", "kind": "knowledge_page",
                  "revisions": {"node-a": "99"}}]
        result = resume_operation(
            self.config,
            JevClient(transport, environ={API_KEY_ENV: API_KEY}, sleep=lambda _: None),
            moved,
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_class"], "superseded")
        self.assertEqual(transport.calls, [])

    def test_both_paths_share_a_benchmark_case_id(self):
        transport = FakeTransport(responses=[TransportResponse(200, success_body(), {})])
        run_operation(make_request(), self.config,
                      JevClient(transport, environ={API_KEY_ENV: API_KEY}, sleep=lambda _: None))
        trace = read_json(trace_path(self.run_dir, self.operation_id, 1))
        self.assertEqual(trace["benchmark_case_id"], make_request()["benchmark_case_id"])
        self.assertEqual(trace["execution_mode"], "jev_assisted")

    def test_no_written_file_leaks_the_key(self):
        transport = FakeTransport(responses=[TransportResponse(200, success_body(), {})])
        run_operation(make_request(), self.config,
                      JevClient(transport, environ={API_KEY_ENV: API_KEY}, sleep=lambda _: None))
        for path in self.run_dir.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=str(path.relative_to(self.run_dir))):
                self.assertNotIn(API_KEY, text)
                self.assertNotIn("Authorization", text)

    def test_the_state_body_only_lives_in_the_persisted_request(self):
        # The whole point of the run directory layout: story text lands in
        # exactly one file, the request the resume path needs, and never in the
        # pending descriptor, the decision context, the trace or the result.
        transport = FakeTransport(responses=[TransportResponse(200, success_body(), {})])
        run_operation(make_request(), self.config,
                      JevClient(transport, environ={API_KEY_ENV: API_KEY}, sleep=lambda _: None))
        carriers = sorted(
            path.relative_to(self.run_dir).as_posix()
            for path in self.run_dir.rglob("*")
            if path.is_file() and MARKER_STATE in path.read_text(encoding="utf-8")
        )
        self.assertEqual(carriers, [f"jev/{self.operation_id}/request.json"])

    def test_the_real_transport_refuses_a_non_allowlisted_endpoint(self):
        with self.assertRaises(JevEndpointError):
            UrllibTransport().send("https://evil.example/v1/systemone", {}, b"{}", 1.0)

    def test_context_sync_keeps_the_schema_valid_through_every_transition(self):
        transport = FakeTransport(responses=[TransportResponse(200, success_body(), {})])
        run_operation(make_request(), self.config,
                      JevClient(transport, environ={}, sleep=lambda _: None))
        sync_decision_context(
            self.config, credential_status="available", pending_call=None,
            resume_cursor="after_execution_choice",
        )
        final = read_decision_context(self.run_dir)
        self.assertEqual(final["credential_status"], "available")
        self.assertEqual(final["selection_status"], "confirmed")


if __name__ == "__main__":
    unittest.main()
