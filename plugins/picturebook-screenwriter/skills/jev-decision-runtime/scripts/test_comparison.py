"""Tests for the two-path comparison gate and report model."""

import unittest

from comparison import (
    COMPARISON_SCHEMA,
    IDENTITY_FIELDS,
    CaseMetrics,
    build_report,
    comparability,
    describe_metric,
    identity_digest,
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


if __name__ == "__main__":
    unittest.main()
