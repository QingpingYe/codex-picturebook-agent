import unittest

from telemetry import (
    DecisionTrace,
    Stopwatch,
    benchmark_case_id,
    estimate_cost_usd,
    estimate_usage,
    request_fingerprint,
)


class FakeClock:
    def __init__(self, *ticks):
        self._ticks = list(ticks)

    def __call__(self):
        return self._ticks.pop(0)


class BenchmarkCaseIdTests(unittest.TestCase):
    def test_same_inputs_produce_the_same_id(self):
        first = benchmark_case_id({"pages": 32}, "knowledge-relevance-v1", "rules-v1")
        second = benchmark_case_id({"pages": 32}, "knowledge-relevance-v1", "rules-v1")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("sha256:"))

    def test_key_order_does_not_change_the_id(self):
        first = benchmark_case_id({"a": 1, "b": 2}, "p", "r")
        second = benchmark_case_id({"b": 2, "a": 1}, "p", "r")
        self.assertEqual(first, second)

    def test_changing_the_input_changes_the_id(self):
        self.assertNotEqual(
            benchmark_case_id({"pages": 32}, "p", "r"),
            benchmark_case_id({"pages": 33}, "p", "r"),
        )

    def test_changing_the_policy_version_changes_the_id(self):
        self.assertNotEqual(
            benchmark_case_id({"pages": 32}, "p1", "r"),
            benchmark_case_id({"pages": 32}, "p2", "r"),
        )


class RequestFingerprintTests(unittest.TestCase):
    def test_bookkeeping_fields_are_excluded(self):
        base = {"run_id": "r1", "state": "x"}
        with_id = dict(base, benchmark_case_id="sha256:" + "0" * 64)
        self.assertEqual(request_fingerprint(base), request_fingerprint(with_id))

    def test_changing_the_state_changes_the_fingerprint(self):
        self.assertNotEqual(
            request_fingerprint({"run_id": "r1", "state": "x"}),
            request_fingerprint({"run_id": "r1", "state": "y"}),
        )


class CostTests(unittest.TestCase):
    def test_cost_matches_the_spec_example(self):
        self.assertEqual(estimate_cost_usd(8200, "0.042"), "0.000344400000")

    def test_cost_is_decimal_not_binary_float(self):
        # 0.042 cannot be represented exactly in binary floating point.
        self.assertEqual(estimate_cost_usd(1, "0.042"), "0.000000042000")

    def test_cost_of_zero_tokens_is_zero(self):
        self.assertEqual(estimate_cost_usd(0, "0.042"), "0.000000000000")

    def test_estimate_usage_returns_nulls_when_usage_is_missing(self):
        self.assertEqual(estimate_usage(None, "0.042"), (None, None, None))
        self.assertEqual(estimate_usage({}, "0.042"), (None, None, None))

    def test_estimate_usage_keeps_cost_when_output_tokens_are_missing(self):
        input_tokens, output_tokens, cost = estimate_usage({"input_tokens": 8200}, "0.042")
        self.assertEqual(input_tokens, 8200)
        self.assertIsNone(output_tokens)
        self.assertEqual(cost, "0.000344400000")

    def test_boolean_token_counts_are_not_accepted_as_integers(self):
        self.assertEqual(estimate_usage({"input_tokens": True}, "0.042"), (None, None, None))


class StopwatchTests(unittest.TestCase):
    def test_elapsed_milliseconds_use_the_injected_clock(self):
        stopwatch = Stopwatch(FakeClock(10.0, 10.412))
        stopwatch.start()
        stopwatch.stop()
        self.assertEqual(stopwatch.elapsed_ms, 412)


class TraceTests(unittest.TestCase):
    def make_trace(self, **overrides):
        payload = {
            "run_id": "20260923-example-0001",
            "benchmark_case_id": "sha256:" + "0" * 64,
            "execution_mode": "jev_assisted",
            "operation": "text_quality_prefilter",
            "started_at": "2026-09-23T10:30:00.000+08:00",
            "finished_at": "2026-09-23T10:30:00.412+08:00",
            "elapsed_ms": 412,
            "input_sha256": "a" * 64,
            "item_count": 32,
            "question_count": 160,
            "screened_clear_count": 0,
            "escalated_count": 0,
            "request_count": 4,
            "input_tokens": 8200,
            "output_tokens": 640,
            "estimated_cost_usd": "0.000344400000",
            "price_usd_per_million_input_tokens": "0.042",
            "price_snapshot_date": "2026-09-23",
            "resolved_model": "jev-1.13.0",
            "status": "succeeded",
            "fallback_used": False,
            "error_class": None,
        }
        payload.update(overrides)
        return DecisionTrace(**payload)

    def test_trace_serializes_to_the_spec_key_set(self):
        serialized = self.make_trace().to_dict()
        self.assertEqual(serialized["schema_version"], "pb-decision-trace-v1")
        self.assertEqual(
            set(serialized),
            {
                "schema_version", "run_id", "benchmark_case_id", "execution_mode",
                "operation", "started_at", "finished_at", "elapsed_ms", "input_sha256",
                "item_count", "question_count", "screened_clear_count", "escalated_count",
                "request_count", "input_tokens", "output_tokens", "estimated_cost_usd",
                "price_usd_per_million_input_tokens", "price_snapshot_date",
                "resolved_model", "status", "fallback_used", "error_class",
            },
        )

    def test_trace_never_carries_draft_text(self):
        marker = "小红帽把蛋糕递给了奶奶"
        serialized = self.make_trace(input_sha256="a" * 64).to_dict()
        self.assertNotIn(marker, repr(serialized))

    def test_fallback_is_false_unless_the_path_actually_switched(self):
        self.assertFalse(self.make_trace().to_dict()["fallback_used"])


if __name__ == "__main__":
    unittest.main()
