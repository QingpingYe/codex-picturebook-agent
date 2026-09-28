"""Tests for the two-path comparison gate and report model."""

import unittest

from comparison import (
    COMPARISON_SCHEMA,
    IDENTITY_FIELDS,
    ITEMS_ENTERED_BY_OPERATION,
    METRIC_NAMES,
    CaseMetrics,
    build_case_report,
    build_report,
    calibration_suggestions,
    comparability,
    describe_metric,
    identity_digest,
    jev_metrics,
    load_llm_usage,
    load_traces,
    operation_metrics,
    report_to_markdown,
    validate_identity,
)


def identity(**overrides):
    payload = {
        "benchmark_case_id": "sha256:" + "0" * 64,
        "input_revision_vector_sha256": "a" * 64,
        "draft_sha256": "b" * 64,
        "policy_version": "text-quality-prefilter-v1",
        "rule_version": "text-quality-prefilter-rules-v1",
        "artifact_params": {"age_band": "3-6", "genre": "温情", "page_count": 32},
    }
    payload.update(overrides)
    return payload


def metrics(path, **overrides):
    payload = {
        "path": path,
        "elapsed_ms": 1000,
        "request_count": 4,
        "input_tokens": 8000,
        "output_tokens": 600,
        "cache_tokens": 0,
        "estimated_cost_usd": "0.000344400000",
        "knowledge_items_entered": 20,
        "quality_items_entered": 12,
        "issues_found": 3,
        "misses_or_disagreements": None,
        "notes": (),
    }
    payload.update(overrides)
    return CaseMetrics(**payload)


class IdentityTests(unittest.TestCase):
    def test_the_pinned_fields_are_the_specs_gate_items(self):
        # spec §10.3 names five gate items; "policy/rule version" is held as two
        # fields so a policy edit without a rule edit is still a mismatch, and
        # the age/genre/page parameters are held as one object compared whole.
        self.assertEqual(IDENTITY_FIELDS, (
            "benchmark_case_id", "input_revision_vector_sha256", "draft_sha256",
            "policy_version", "rule_version", "artifact_params",
        ))

    def test_a_complete_identity_validates(self):
        validate_identity(identity())

    def test_each_missing_field_is_refused(self):
        for field in IDENTITY_FIELDS:
            with self.subTest(field=field):
                broken = identity()
                del broken[field]
                with self.assertRaises(ValueError):
                    validate_identity(broken)

    def test_a_blank_string_field_is_refused(self):
        with self.assertRaises(ValueError):
            validate_identity(identity(draft_sha256="   "))

    def test_an_empty_artifact_params_object_is_refused(self):
        with self.assertRaises(ValueError):
            validate_identity(identity(artifact_params={}))

    def test_the_identity_digest_ignores_key_order(self):
        self.assertEqual(
            identity_digest(identity()),
            identity_digest(identity(artifact_params={
                "page_count": 32, "genre": "温情", "age_band": "3-6",
            })),
        )

    def test_the_identity_digest_changes_with_a_revision_vector(self):
        self.assertNotEqual(
            identity_digest(identity()),
            identity_digest(identity(input_revision_vector_sha256="c" * 64)),
        )


    def test_the_identity_digest_ignores_a_key_outside_the_gate(self):
        # The digest names the shared case, and the gate never reads a key
        # outside IDENTITY_FIELDS: letting one move the digest would give the
        # same case two names while `comparability` still calls it comparable.
        self.assertEqual(
            identity_digest(identity()),
            identity_digest(identity(model_label="jev-1.13.0")),
        )

    def test_the_identity_digest_refuses_an_incomplete_identity(self):
        incomplete = identity()
        del incomplete["draft_sha256"]
        with self.assertRaises(ValueError) as raised:
            identity_digest(incomplete)
        self.assertIn("draft_sha256", str(raised.exception))


class ComparabilityTests(unittest.TestCase):
    def test_matching_identities_are_comparable(self):
        comparable, reasons = comparability(identity(), identity())
        self.assertTrue(comparable)
        self.assertEqual(reasons, ())

    def test_a_different_benchmark_case_is_not_comparable(self):
        comparable, reasons = comparability(
            identity(), identity(benchmark_case_id="sha256:" + "1" * 64)
        )
        self.assertFalse(comparable)
        self.assertIn("benchmark_case_id", reasons[0])

    def test_a_different_draft_is_not_comparable(self):
        comparable, reasons = comparability(identity(), identity(draft_sha256="d" * 64))
        self.assertFalse(comparable)
        self.assertTrue(any("draft_sha256" in reason for reason in reasons))

    def test_every_mismatched_field_is_reported(self):
        comparable, reasons = comparability(
            identity(),
            identity(policy_version="text-quality-prefilter-v2",
                     rule_version="text-quality-prefilter-rules-v2",
                     input_revision_vector_sha256="e" * 64),
        )
        self.assertFalse(comparable)
        self.assertEqual(len(reasons), 3)

    def test_different_artifact_params_are_not_comparable(self):
        comparable, reasons = comparability(
            identity(),
            identity(artifact_params={"age_band": "6-8", "genre": "温情", "page_count": 32}),
        )
        self.assertFalse(comparable)
        self.assertTrue(any("artifact_params" in reason for reason in reasons))

    def test_an_identity_that_lacks_a_field_is_never_comparable(self):
        # Two runs that both omit the same field are not "the same work"; the
        # gate is fail-closed, so a hole on either side becomes a reason.
        for shape in ("absent", "null"):
            with self.subTest(shape=shape):
                left = identity()
                right = identity()
                if shape == "absent":
                    del left["rule_version"]
                    del right["rule_version"]
                else:
                    left["rule_version"] = None
                    right["rule_version"] = None
                comparable, reasons = comparability(left, right)
                self.assertFalse(comparable)
                self.assertTrue(any("rule_version" in reason for reason in reasons))

    def test_a_hole_on_one_side_only_is_reported_for_that_side(self):
        incomplete = identity()
        del incomplete["draft_sha256"]
        comparable, reasons = comparability(identity(), incomplete)
        self.assertFalse(comparable)
        self.assertEqual(len(reasons), 1)
        self.assertIn("draft_sha256", reasons[0])


class ReportTests(unittest.TestCase):
    def report(self, **overrides):
        payload = build_report(
            identity=identity(),
            comparable=True,
            reasons=(),
            llm=metrics("llm", input_tokens=54000, estimated_cost_usd="1.234500000000",
                        request_count=12, elapsed_ms=180000),
            jev_assisted=metrics("jev_assisted"),
            calibration=(),
        )
        payload.update(overrides)
        return payload

    def test_the_report_declares_its_schema(self):
        self.assertEqual(self.report()["schema_version"], COMPARISON_SCHEMA)

    def test_the_report_keeps_both_paths_side_by_side(self):
        report = self.report()
        self.assertEqual(report["llm"]["path"], "llm")
        self.assertEqual(report["jev_assisted"]["path"], "jev_assisted")

    def test_the_report_carries_the_comparability_verdict(self):
        report = build_report(identity=identity(), comparable=False,
                              reasons=("benchmark_case_id differs",),
                              llm=metrics("llm"), jev_assisted=metrics("jev_assisted"),
                              calibration=())
        self.assertFalse(report["comparable"])
        self.assertEqual(report["comparability_reasons"],
                         ["benchmark_case_id differs"])

    def test_markdown_names_every_metric_pair(self):
        text = report_to_markdown(self.report())
        self.assertIn("## 路径对比", text)
        self.assertIn("input_tokens", text)
        self.assertIn("estimated_cost_usd", text)

    def test_markdown_says_which_side_a_missing_measurement_belongs_to(self):
        # A row reading `100 | None` makes the reader guess whether the plain-LLM
        # side or the Jev side never measured it.
        report = self.report()
        report["jev_assisted"] = dict(report["jev_assisted"], cache_tokens=None)
        row = next(
            line for line in report_to_markdown(report).splitlines()
            if line.startswith("| cache_tokens ")
        )
        self.assertIn("未测得（Jev 辅助）", row)
        self.assertNotIn("None", row)

    def test_markdown_states_when_the_runs_are_not_comparable(self):
        text = report_to_markdown(build_report(
            identity=identity(), comparable=False, reasons=("draft_sha256 differs",),
            llm=metrics("llm"), jev_assisted=metrics("jev_assisted"), calibration=(),
        ))
        self.assertIn("不可比较", text)
        self.assertIn("draft_sha256 differs", text)

    def test_an_all_clear_markdown_report_omits_the_warning(self):
        self.assertNotIn("不可比较", report_to_markdown(self.report()))


class DescribeMetricTests(unittest.TestCase):
    def test_a_known_ratio_is_described_as_a_ratio(self):
        self.assertEqual(describe_metric("input_tokens", 8000, 4000), "8000 → 4000 (0.50×)")

    def test_a_zero_jev_side_does_not_divide_by_zero(self):
        self.assertIn("n/a", describe_metric("elapsed_ms", 1000, 0))

    def test_a_missing_value_is_reported_as_unknown(self):
        self.assertIn("unknown", describe_metric("cache_tokens", 100, None))


import json
import tempfile
from pathlib import Path

from decision_contract import default_policy_path, load_policy
from jev_runner import write_atomic


def trace(operation, **overrides):
    payload = {
        "schema_version": "pb-decision-trace-v1",
        "run_id": "20260923-example-0001",
        "benchmark_case_id": "sha256:" + "0" * 64,
        "execution_mode": "jev_assisted",
        "operation": operation,
        "started_at": "2026-09-23T10:30:00.000+08:00",
        "finished_at": "2026-09-23T10:30:00.412+08:00",
        "elapsed_ms": 412,
        "input_sha256": "a" * 64,
        "item_count": 10,
        "question_count": 40,
        "screened_clear_count": 6,
        "escalated_count": 4,
        "request_count": 1,
        "input_tokens": 2000,
        "output_tokens": 100,
        "estimated_cost_usd": "0.000084000000",
        "price_usd_per_million_input_tokens": "0.042",
        "price_snapshot_date": "2026-09-23",
        "resolved_model": "jev-1.13.0",
        "status": "succeeded",
        "fallback_used": False,
        "error_class": None,
    }
    payload.update(overrides)
    return payload


LLM_USAGE = {
    "identity": identity(),
    "model_label": "gpt-5-codex",
    "source": "cc-switch-manual-entry",
    "elapsed_ms": 180000,
    "request_count": 12,
    "input_tokens": 54000,
    "output_tokens": 9000,
    "cache_tokens": 12000,
    "estimated_cost_usd": "1.234500000000",
    "knowledge_items_entered": 180,
    "quality_items_entered": 160,
    "issues_found": 7,
    "misses_or_disagreements": 1,
}


class TraceLoadingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, operation, attempt, payload):
        write_atomic(
            self.run_dir / "jev" / operation / "trace" / f"{attempt:04d}.json", payload
        )

    def test_traces_are_read_from_the_run_directory(self):
        self._write("text_quality_prefilter", 1, trace("text_quality_prefilter"))
        traces = load_traces(self.run_dir)
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["operation"], "text_quality_prefilter")

    def test_an_empty_run_directory_yields_no_traces(self):
        self.assertEqual(load_traces(self.run_dir), ())

    def test_two_attempts_of_one_operation_count_their_verdicts_once(self):
        # A resumed or retried operation writes one trace per attempt. Summing
        # them would double the cleared and escalated totals the whole
        # comparison is built on, so only the terminal attempt is counted.
        self._write("text_quality_prefilter", 1, trace(
            "text_quality_prefilter", status="failed",
            screened_clear_count=0, escalated_count=0,
        ))
        self._write("text_quality_prefilter", 2, trace("text_quality_prefilter"))
        traces = load_traces(self.run_dir)
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["status"], "succeeded")
        metrics = jev_metrics(traces)
        self.assertEqual(metrics.issues_found, 4)
        self.assertIn("screened_clear=6", metrics.notes)

    def test_a_waiting_attempt_never_displaces_an_earlier_measurement(self):
        # Supplying a missing key and resuming appends a second attempt. A wait
        # is not a measurement, so it must not replace the attempt that really
        # ran and hand the report the user's own configuration time.
        self._write("text_quality_prefilter", 1, trace("text_quality_prefilter"))
        self._write("text_quality_prefilter", 2, trace(
            "text_quality_prefilter", status="waiting_for_jev_key",
            elapsed_ms=90_000, request_count=0, input_tokens=None,
            estimated_cost_usd=None,
        ))
        traces = load_traces(self.run_dir)
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["status"], "succeeded")
        self.assertEqual(jev_metrics(traces).elapsed_ms, 412)

    def test_a_fallback_run_is_not_a_jev_sample(self):
        # A run that switched back to the plain LLM mid-way measures a hybrid
        # path, not the Jev-assisted path, so its time and cost must stay out
        # of the Jev side of the comparison.
        metrics = jev_metrics((
            trace("text_quality_prefilter"),
            trace("knowledge_relevance", fallback_used=True, input_tokens=99_000,
                  estimated_cost_usd="9.000000000000", elapsed_ms=500_000),
        ))
        self.assertEqual(metrics.input_tokens, 2000)
        self.assertEqual(metrics.elapsed_ms, 412)

    def test_the_report_names_the_operations_the_jev_side_ran(self):
        metrics = jev_metrics((
            trace("knowledge_relevance"),
            trace("text_quality_prefilter"),
        ))
        self.assertIn(
            "operations=knowledge_relevance,text_quality_prefilter", metrics.notes
        )

    def test_jev_metrics_sum_the_traces(self):
        metrics = jev_metrics((
            trace("text_quality_prefilter"),
            trace("knowledge_relevance", input_tokens=1000, request_count=2,
                  estimated_cost_usd="0.000042000000", elapsed_ms=200),
        ))
        self.assertEqual(metrics.path, "jev_assisted")
        self.assertEqual(metrics.input_tokens, 3000)
        self.assertEqual(metrics.request_count, 3)
        self.assertEqual(metrics.elapsed_ms, 612)
        self.assertEqual(metrics.estimated_cost_usd, "0.000126000000")

    def test_a_zero_cost_total_stays_a_plain_fixed_point_decimal(self):
        # `str(Decimal("0.000000000000"))` is "0E-12", which is not the fixed
        # point string the trace contract promises for a cost column.
        metrics = jev_metrics((
            trace("text_quality_prefilter", estimated_cost_usd="0.000000000000"),
        ))
        self.assertEqual(metrics.estimated_cost_usd, "0.000000000000")

    def test_a_waiting_trace_does_not_pollute_the_measurement(self):
        # A waiting run never dispatched, so it must not enter the speed and
        # cost sample at all.
        metrics = jev_metrics((
            trace("text_quality_prefilter"),
            trace("knowledge_relevance", status="waiting_for_jev_key",
                  input_tokens=None, output_tokens=None,
                  estimated_cost_usd=None, elapsed_ms=90_000, request_count=0),
        ))
        self.assertEqual(metrics.input_tokens, 2000)
        self.assertEqual(metrics.elapsed_ms, 412)

    def test_the_jev_side_reports_no_cache_measurement_rather_than_zero(self):
        # The trace contract has no cache-token field, so the Jev column has
        # nothing to report. A hard-coded zero reads as "this run was measured
        # and used no cache" — a measurement nobody took — where `null` is the
        # same "not reported" the contract uses for a trace with no usage.
        metrics = jev_metrics((trace("text_quality_prefilter"),))
        self.assertIsNone(metrics.cache_tokens)

    def test_an_ambiguous_outcome_is_counted_but_not_as_cost(self):
        # An attempt whose outcome is unknown did reach the service and may
        # have been billed, so its cost is unknown rather than zero: a total
        # built on a zero would make a bill nobody measured look measured.
        # `runtime_failure` is what carries the ambiguity instead.
        metrics = jev_metrics((
            trace("text_quality_prefilter", status="outcome_unknown",
                  input_tokens=None, estimated_cost_usd=None),
        ))
        self.assertIsNone(metrics.estimated_cost_usd)
        self.assertIn("runtime_failure=1", metrics.notes)

    def test_jev_metrics_report_the_escalation_volume(self):
        metrics = jev_metrics((
            trace("text_quality_prefilter"),
            trace("text_quality_prefilter", attempt=2, escalated_count=7,
                  screened_clear_count=3, item_count=10),
        ))
        # The row is "how much of this operation went to the plain LLM", so it
        # reads the operation's own escalation count: 4 escalated in the first
        # trace plus 7 in the second. `item_count` (10) is the number of
        # authority pages those requests cited, which is a different quantity.
        self.assertEqual(metrics.quality_items_entered, 11)
        self.assertIn("escalated=11", metrics.notes)

    def test_the_two_items_entered_rows_read_their_own_operation(self):
        # The rows count different units — chunks of knowledge and page
        # dimensions — so one number cannot stand in for both, and a run that
        # only screened pages must not report the knowledge row at all.
        both = jev_metrics((
            trace("knowledge_relevance", escalated_count=3, screened_clear_count=7,
                  item_count=10),
            trace("text_quality_prefilter", escalated_count=11,
                  screened_clear_count=21, item_count=32),
        ))
        self.assertEqual(both.knowledge_items_entered, 3)
        self.assertEqual(both.quality_items_entered, 11)
        pages_only = jev_metrics((trace("text_quality_prefilter"),))
        self.assertIsNone(pages_only.knowledge_items_entered)
        self.assertEqual(pages_only.quality_items_entered, 4)

    def test_the_two_entered_rows_are_built_from_one_mapping(self):
        # Both rows are read out of `ITEMS_ENTERED_BY_OPERATION`, so the pair a
        # reader finds there is the pair the report renders — not a leftover
        # copy of the same knowledge that has since drifted.
        self.assertEqual(ITEMS_ENTERED_BY_OPERATION, (
            ("knowledge_relevance", "knowledge_items_entered"),
            ("text_quality_prefilter", "quality_items_entered"),
        ))
        for _operation, metric in ITEMS_ENTERED_BY_OPERATION:
            with self.subTest(metric=metric):
                self.assertIn(metric, METRIC_NAMES)

    def test_a_succeeded_trace_that_recorded_no_verdicts_is_named(self):
        # A batch settles as `succeeded` only after deciding something, so a
        # zero pair on a succeeded trace is a hole rather than a clean run: the
        # `resume` entry point has no operation routing to hand the runner.
        # The counts beside it then cover only the batches that recorded theirs,
        # and the note says so where the reader looks.
        metrics = jev_metrics((
            trace("text_quality_prefilter", screened_clear_count=0,
                  escalated_count=0),
        ))
        self.assertIn("traces_without_verdicts=1", metrics.notes)

    def test_a_failed_attempt_is_not_a_trace_without_verdicts(self):
        # A batch that never settled decided nothing, so its zero pair is the
        # truth rather than a missing measurement: it is reported through
        # `runtime_failure`, which is what keeps the failure out of calibration.
        entries = operation_metrics((
            trace("text_quality_prefilter", status="failed",
                  screened_clear_count=0, escalated_count=0),
            trace("text_quality_prefilter", attempt=2),
        ))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["runtime_failure"], 1)
        self.assertEqual(entries[0]["traces_without_verdicts"], 0)
        self.assertNotIn(
            "traces_without_verdicts",
            " ".join(jev_metrics((
                trace("text_quality_prefilter", status="failed",
                      screened_clear_count=0, escalated_count=0),
            )).notes),
        )

    def test_two_runs_in_one_directory_are_named_not_summed_silently(self):
        # Two run ids of one operation are two measurements. Their sum may not
        # read as a single run, so it is named where the number is.
        metrics = jev_metrics((
            trace("text_quality_prefilter"),
            trace("text_quality_prefilter", run_id="20260923-example-0002",
                  input_tokens=1000, elapsed_ms=100),
        ))
        self.assertIn("runs=2", metrics.notes)
        self.assertEqual(metrics.input_tokens, 3000)

    def test_one_run_gets_no_run_count_note(self):
        metrics = jev_metrics((trace("text_quality_prefilter"),))
        self.assertFalse([note for note in metrics.notes if note.startswith("runs=")])


class LlmUsageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "llm-usage.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, payload):
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return self.path

    def test_a_complete_entry_is_read_into_metrics_and_identity(self):
        loaded_identity, metrics = load_llm_usage(self._write(LLM_USAGE))
        self.assertEqual(loaded_identity["benchmark_case_id"], "sha256:" + "0" * 64)
        self.assertEqual(metrics.path, "llm")
        self.assertEqual(metrics.input_tokens, 54000)
        self.assertEqual(metrics.cache_tokens, 12000)
        self.assertEqual(metrics.estimated_cost_usd, "1.234500000000")

    def test_an_entry_without_an_identity_is_refused(self):
        payload = dict(LLM_USAGE)
        del payload["identity"]
        with self.assertRaises(ValueError):
            load_llm_usage(self._write(payload))

    def test_the_source_must_name_a_manual_entry(self):
        # If this ever records a database path, the plugin has started reading
        # a private store it promised not to touch.
        payload = dict(LLM_USAGE, source="cc-switch-sqlite:/home/u/.cc-switch.db")
        with self.assertRaises(ValueError):
            load_llm_usage(self._write(payload))

    def test_metrics_may_be_absent_and_read_as_null(self):
        payload = dict(LLM_USAGE)
        for name in ("cache_tokens", "misses_or_disagreements", "estimated_cost_usd"):
            payload[name] = None
        _, metrics = load_llm_usage(self._write(payload))
        self.assertIsNone(metrics.cache_tokens)
        self.assertIsNone(metrics.misses_or_disagreements)
        self.assertIsNone(metrics.estimated_cost_usd)


class CalibrationSuggestionTests(unittest.TestCase):
    """The advice is per operation, from that operation's own numbers."""

    def _report(self, *, comparable=True, **operations):
        """The part of a comparison report `calibration_suggestions` reads.

        Each keyword is one measured operation and its `(escalated,
        screened_clear, runtime_failure)` counts. Run-level notes are absent on
        purpose: a blended rate across operations is what the per-operation
        advice exists to stop using.
        """

        entries = []
        for operation, counts in operations.items():
            escalated, screened_clear = counts[0], counts[1]
            failed = counts[2] if len(counts) > 2 else 0
            entries.append({
                "run_id": "20260923-example-0001",
                "operation": operation,
                "screened_clear_count": screened_clear,
                "escalated_count": escalated,
                "runtime_failure": failed,
            })
        return {"comparable": comparable, "jev_operations": entries}

    def _entry(self, report, operation="text_quality_prefilter"):
        suggestions = calibration_suggestions(
            load_policy(default_policy_path()), report
        )
        return next(s for s in suggestions if s["operation"] == operation)

    def test_an_experimental_operation_with_any_failure_stays_experimental(self):
        entry = self._entry(self._report(text_quality_prefilter=(1, 9, 1)))
        self.assertEqual(entry["suggestion"], "keep_experimental")

    def test_a_very_high_escalation_rate_suggests_reviewing_thresholds(self):
        entry = self._entry(self._report(text_quality_prefilter=(10, 0)))
        self.assertEqual(entry["suggestion"], "review_thresholds")

    def test_an_experimental_operation_is_never_auto_promoted(self):
        entry = self._entry(self._report(text_quality_prefilter=(1, 99)))
        self.assertEqual(entry["current"], "experimental")
        self.assertIn(entry["suggestion"],
                      ("eligible_for_calibrated", "keep_experimental"))
        self.assertNotEqual(entry["current"], entry["suggestion"])

    def test_every_measured_operation_gets_a_suggestion(self):
        suggestions = calibration_suggestions(
            load_policy(default_policy_path()),
            self._report(knowledge_relevance=(1, 9), text_quality_prefilter=(1, 9)),
        )
        self.assertEqual(
            [entry["operation"] for entry in suggestions],
            ["knowledge_relevance", "text_quality_prefilter"],
        )

    def test_an_operation_this_run_never_measured_gets_no_suggestion(self):
        # "No measurement" is not evidence in either direction, so the report
        # says nothing about the operation instead of suggesting a status for
        # numbers it never saw.
        suggestions = calibration_suggestions(
            load_policy(default_policy_path()),
            self._report(text_quality_prefilter=(1, 9)),
        )
        self.assertEqual([entry["operation"] for entry in suggestions],
                         ["text_quality_prefilter"])

    def test_each_operation_is_judged_by_its_own_numbers(self):
        # The measured case: knowledge escalated nothing while the page
        # pre-screen escalated almost everything. Blended into one rate the
        # pair looked acceptable and both operations were told they could be
        # promoted, including the one whose thresholds are too tight.
        report = self._report(knowledge_relevance=(0, 40),
                              text_quality_prefilter=(47, 3))
        self.assertEqual(self._entry(report, "knowledge_relevance")["suggestion"],
                         "eligible_for_calibrated")
        entry = self._entry(report, "text_quality_prefilter")
        self.assertEqual(entry["suggestion"], "review_thresholds")
        self.assertIn("94%", entry["reason"])

    def test_an_incomparable_report_still_produces_suggestions(self):
        report = self._report(comparable=False, text_quality_prefilter=(1, 9))
        entry = self._entry(report)
        self.assertEqual(entry["suggestion"], "keep_experimental")
        self.assertIn("不可比较", entry["reason"])

    def test_an_operation_that_lost_its_verdict_counts_is_never_promoted(self):
        # The counts look perfect — nothing escalated out of 14 items — but one
        # of the succeeded traces never recorded what it decided, so the ratio
        # covers only the batches that recorded theirs. That may not be read as
        # "cleared enough to stop re-checking", and the reason has to name why.
        report = self._report(text_quality_prefilter=(0, 14))
        report["jev_operations"][0]["traces_without_verdicts"] = 1
        entry = self._entry(report)
        self.assertEqual(entry["suggestion"], "keep_experimental")
        self.assertIn("没有记下判定计数", entry["reason"])

    def test_a_high_escalation_share_is_not_advice_when_counts_are_missing(self):
        # The other direction of the same hole: with a batch's counts missing,
        # even the "review the thresholds" reading rests on a partial number.
        report = self._report(text_quality_prefilter=(9, 1))
        report["jev_operations"][0]["traces_without_verdicts"] = 2
        entry = self._entry(report)
        self.assertEqual(entry["suggestion"], "keep_experimental")
        self.assertIn("2 条", entry["reason"])

    def test_a_trace_naming_an_unknown_operation_produces_no_advice(self):
        # A hand-written trace cannot be priced against a policy, so it gives
        # no advice — the entry is still listed in `jev_operations` for the
        # reader, and the report does not raise.
        report = self._report(not_an_operation=(1, 9))
        self.assertEqual(calibration_suggestions(
            load_policy(default_policy_path()), report), ())

    def test_a_report_without_jev_operations_produces_no_advice(self):
        self.assertEqual(calibration_suggestions(
            load_policy(default_policy_path()), {"comparable": True}), ())


class BuildCaseReportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)
        write_atomic(
            self.run_dir / "jev" / "text_quality_prefilter" / "trace" / "0001.json",
            trace("text_quality_prefilter"),
        )
        self.usage_path = self.run_dir / "llm-usage.json"
        self.usage_path.write_text(json.dumps(LLM_USAGE, ensure_ascii=False),
                                   encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _rewrite_usage(self, **identity_overrides):
        payload = json.loads(self.usage_path.read_text(encoding="utf-8"))
        payload["identity"].update(identity_overrides)
        self.usage_path.write_text(json.dumps(payload, ensure_ascii=False),
                                   encoding="utf-8")
        return build_case_report(
            run_dir=self.run_dir, llm_usage_path=self.usage_path,
            policy=load_policy(default_policy_path()),
        )

    def test_matching_identities_produce_a_comparable_report(self):
        report = build_case_report(
            run_dir=self.run_dir, llm_usage_path=self.usage_path,
            policy=load_policy(default_policy_path()),
        )
        self.assertTrue(report["comparable"])
        self.assertEqual(report["comparability_reasons"], [])

    def test_a_case_id_the_run_does_not_attest_is_incomparable(self):
        # The trace is the part of the run this report can hold the entry
        # against, and what it carries is the derived case id. An entry whose
        # case id no trace belongs to describes a different run, so the pair
        # must not be presented as comparable.
        report = self._rewrite_usage(benchmark_case_id="sha256:" + "1" * 64)
        self.assertFalse(report["comparable"])
        self.assertTrue(any("benchmark_case_id" in reason
                            for reason in report["comparability_reasons"]))

    def test_a_policy_version_the_run_never_used_is_incomparable(self):
        # A trace names the operation it ran, and the policy names that
        # operation's version, so a declared version that no traced operation
        # used is provably not this run's policy.
        report = self._rewrite_usage(policy_version="text-quality-prefilter-v2")
        self.assertFalse(report["comparable"])
        self.assertTrue(any("policy_version" in reason
                            for reason in report["comparability_reasons"]))

    def test_the_report_says_which_identity_fields_the_run_attested(self):
        # Five of the six identity fields have no second copy on disk: a trace
        # holds the derived case id, not the draft hash, the revision vector or
        # the artifact parameters. The report has to name the fields it checked
        # rather than imply that all six were verified.
        report = build_case_report(
            run_dir=self.run_dir, llm_usage_path=self.usage_path,
            policy=load_policy(default_policy_path()),
        )
        self.assertEqual(report["identity_attestation"]["verified"],
                         ["benchmark_case_id", "policy_version"])
        self.assertIn("draft_sha256", report["identity_attestation"]["declared_only"])

    def test_traces_from_another_case_are_not_summed_in(self):
        # Reusing a run directory for a second case leaves the earlier traces
        # behind. Summing them would silently inflate this case's tokens and
        # cost while still reporting the two runs as comparable.
        write_atomic(
            self.run_dir / "jev" / "knowledge_relevance" / "trace" / "0001.json",
            trace("knowledge_relevance", benchmark_case_id="sha256:" + "f" * 64),
        )
        report = build_case_report(
            run_dir=self.run_dir, llm_usage_path=self.usage_path,
            policy=load_policy(default_policy_path()),
        )
        self.assertTrue(report["comparable"])
        self.assertEqual(report["traces"], 1)
        self.assertEqual(report["foreign_traces"], 1)
        self.assertEqual(report["jev_assisted"]["input_tokens"], 2000)

    def test_a_directory_that_names_only_other_cases_is_not_comparable(self):
        # A directory reused twice leaves two earlier cases' traces behind, and
        # an entry declaring a third has nothing on disk to hold its case id
        # against: the pair would be "the same case" only because the entry says
        # so, while the Jev column is empty of traces for it.
        other_a = "sha256:" + "a" * 64
        other_b = "sha256:" + "b" * 64
        write_atomic(
            self.run_dir / "jev" / "text_quality_prefilter" / "trace" / "0001.json",
            trace("text_quality_prefilter", benchmark_case_id=other_a),
        )
        write_atomic(
            self.run_dir / "jev" / "knowledge_relevance" / "trace" / "0001.json",
            trace("knowledge_relevance", benchmark_case_id=other_b),
        )
        report = build_case_report(
            run_dir=self.run_dir, llm_usage_path=self.usage_path,
            policy=load_policy(default_policy_path()),
        )
        self.assertFalse(report["comparable"])
        reasons = [reason for reason in report["comparability_reasons"]
                   if "benchmark_case_id" in reason]
        self.assertTrue(reasons)
        self.assertIn(other_a, reasons[0])
        self.assertIn(other_b, reasons[0])
        self.assertNotIn("benchmark_case_id",
                         report["identity_attestation"]["verified"])

    def test_declaring_one_of_the_cases_in_the_directory_still_compares(self):
        # The flip side of the reason above: it means "none of these traces is
        # this case", not "this directory holds more than one case". A directory
        # that holds this case beside another one still compares, with the
        # foreign traces left out of the numbers.
        write_atomic(
            self.run_dir / "jev" / "knowledge_relevance" / "trace" / "0001.json",
            trace("knowledge_relevance", benchmark_case_id="sha256:" + "b" * 64),
        )
        report = build_case_report(
            run_dir=self.run_dir, llm_usage_path=self.usage_path,
            policy=load_policy(default_policy_path()),
        )
        self.assertTrue(report["comparable"])
        self.assertEqual(report["comparability_reasons"], [])
        self.assertEqual(report["traces"], 1)
        self.assertEqual(report["foreign_traces"], 1)
        self.assertEqual(report["jev_assisted"]["input_tokens"], 2000)

    def test_a_numeric_cost_is_refused_with_a_quoting_hint(self):
        # The likely mistake while copying numbers out of CC Switch by hand is
        # writing a JSON number. A binary float would then leak into the cost
        # column, so the entry is refused and the fix is spelled out.
        payload = json.loads(self.usage_path.read_text(encoding="utf-8"))
        payload["estimated_cost_usd"] = 1.2345
        self.usage_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        with self.assertRaises(ValueError) as raised:
            build_case_report(
                run_dir=self.run_dir, llm_usage_path=self.usage_path,
                policy=load_policy(default_policy_path()),
            )
        self.assertIn("decimal string", str(raised.exception))

    def test_an_empty_run_directory_reports_no_jev_measurement(self):
        empty = self.run_dir / "empty"
        empty.mkdir()
        report = build_case_report(
            run_dir=empty, llm_usage_path=self.usage_path,
            policy=load_policy(default_policy_path()),
        )
        self.assertIsNone(report["jev_assisted"]["input_tokens"])

    def test_the_report_carries_the_calibration_suggestions(self):
        report = build_case_report(
            run_dir=self.run_dir, llm_usage_path=self.usage_path,
            policy=load_policy(default_policy_path()),
        )
        # Only the operation this run measured is advised on, and the advice
        # comes from that operation's own counts.
        self.assertEqual(
            [entry["operation"] for entry in report["calibration"]],
            ["text_quality_prefilter"],
        )
        measured = report["jev_operations"][0]
        self.assertEqual(measured["screened_clear_count"], 6)
        self.assertEqual(measured["escalated_count"], 4)

    def test_a_directory_holding_two_runs_is_not_comparable(self):
        # The same draft screened twice under two run ids is two measurements,
        # and the run directory cannot say which of them the hand-copied
        # plain-LLM usage describes. Summing them silently would double every
        # number in the Jev column.
        write_atomic(
            self.run_dir / "jev" / "20260923-example-0002-text_quality_prefilter"
            / "trace" / "0001.json",
            trace("text_quality_prefilter", run_id="20260923-example-0002"),
        )
        report = build_case_report(
            run_dir=self.run_dir, llm_usage_path=self.usage_path,
            policy=load_policy(default_policy_path()),
        )
        self.assertFalse(report["comparable"])
        self.assertTrue(any("runs" in reason
                            for reason in report["comparability_reasons"]))
        self.assertEqual(
            report["runs"], ["20260923-example-0001", "20260923-example-0002"]
        )
        # The sum is still labelled where the number is, and each run's own
        # numbers stay readable beside it.
        self.assertIn("runs=2", report["jev_assisted"]["notes"])
        self.assertEqual([entry["run_id"] for entry in report["jev_operations"]],
                         ["20260923-example-0001", "20260923-example-0002"])

    def test_one_run_is_not_reported_as_a_multi_run_directory(self):
        report = build_case_report(
            run_dir=self.run_dir, llm_usage_path=self.usage_path,
            policy=load_policy(default_policy_path()),
        )
        self.assertEqual(report["runs"], ["20260923-example-0001"])
        self.assertFalse([reason for reason in report["comparability_reasons"]
                          if "runs" in reason])

    def test_a_trace_that_names_no_case_id_never_reads_as_comparable(self):
        # Nothing on disk then attests the case the entry declares, and the two
        # sides would be "the same case" only because both were copied from the
        # same hand-written line.
        write_atomic(
            self.run_dir / "jev" / "text_quality_prefilter" / "trace" / "0001.json",
            trace("text_quality_prefilter", benchmark_case_id=None),
        )
        report = build_case_report(
            run_dir=self.run_dir, llm_usage_path=self.usage_path,
            policy=load_policy(default_policy_path()),
        )
        self.assertFalse(report["comparable"])
        self.assertTrue(any("benchmark_case_id" in reason
                            for reason in report["comparability_reasons"]))
        self.assertNotIn("benchmark_case_id", report["identity_attestation"]["verified"])

    def test_a_directory_with_no_trace_is_not_a_comparison(self):
        empty = self.run_dir / "empty"
        empty.mkdir()
        report = build_case_report(
            run_dir=empty, llm_usage_path=self.usage_path,
            policy=load_policy(default_policy_path()),
        )
        self.assertFalse(report["comparable"])
        self.assertTrue(any("benchmark_case_id" in reason
                            for reason in report["comparability_reasons"]))

    def test_the_markdown_reports_the_traces_it_left_out(self):
        write_atomic(
            self.run_dir / "jev" / "knowledge_relevance" / "trace" / "0001.json",
            trace("knowledge_relevance", benchmark_case_id="sha256:" + "f" * 64),
        )
        report = build_case_report(
            run_dir=self.run_dir, llm_usage_path=self.usage_path,
            policy=load_policy(default_policy_path()),
        )
        text = report_to_markdown(report)
        self.assertIn("trace 数：1（属于其它 case：1）", text)


if __name__ == "__main__":
    unittest.main()
