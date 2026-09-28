"""Phase 4 integration: the comparison report and its export boundary."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
for _relative in ("jev-decision-runtime", "knowledge-loader", "pre_output-baseline",
                  "session-export"):
    sys.path.insert(0, str(ROOT / "skills" / _relative / "scripts"))

from compare_cli import main as compare_main  # noqa: E402
from comparison import COMPARISON_SCHEMA, build_case_report, report_to_markdown  # noqa: E402
from decision_contract import default_policy_path, load_policy  # noqa: E402
from jev_runner import write_atomic  # noqa: E402

CASE_ID = "sha256:" + "0" * 64


def identity():
    return {
        "benchmark_case_id": CASE_ID,
        "input_revision_vector_sha256": "a" * 64,
        "draft_sha256": "b" * 64,
        "policy_version": "text-quality-prefilter-v1",
        "rule_version": "text-quality-prefilter-rules-v1",
        "artifact_params": {"age_band": "3-6", "genre": "温情", "page_count": 32},
    }


def trace(operation, **overrides):
    payload = {
        "schema_version": "pb-decision-trace-v1",
        "run_id": "20260923-integration-0001",
        "benchmark_case_id": CASE_ID,
        "execution_mode": "jev_assisted",
        "operation": operation,
        "started_at": "2026-09-23T10:30:00.000+08:00",
        "finished_at": "2026-09-23T10:30:00.400+08:00",
        "elapsed_ms": 400,
        "input_sha256": "a" * 64,
        "item_count": 32,
        "question_count": 160,
        "screened_clear_count": 21,
        "escalated_count": 11,
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
    return payload


def llm_usage(**overrides):
    payload = {
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
    payload.update(overrides)
    return payload


class Phase4IntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.run_dir = self.root / "run"
        write_atomic(
            self.run_dir / "jev" / "text_quality_prefilter" / "trace" / "0001.json",
            trace("text_quality_prefilter"),
        )
        write_atomic(
            self.run_dir / "jev" / "knowledge_relevance" / "trace" / "0001.json",
            trace("knowledge_relevance", input_tokens=4000, output_tokens=300,
                  request_count=2, elapsed_ms=200, estimated_cost_usd="0.000168000000"),
        )
        self.usage = self.root / "llm-usage.json"
        self.usage.write_text(json.dumps(llm_usage(), ensure_ascii=False),
                              encoding="utf-8")
        self.plugin = self.root / "plugin"
        self.plugin.mkdir()
        self.policy = load_policy(default_policy_path())

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_report_sums_both_jev_traces(self):
        report = build_case_report(run_dir=self.run_dir, llm_usage_path=self.usage,
                                   policy=self.policy)
        self.assertTrue(report["comparable"])
        self.assertEqual(report["jev_assisted"]["input_tokens"], 12200)
        self.assertEqual(report["jev_assisted"]["request_count"], 6)
        self.assertEqual(report["jev_assisted"]["elapsed_ms"], 600)
        self.assertEqual(report["jev_assisted"]["estimated_cost_usd"],
                         "0.000512400000")

    def test_the_llm_side_comes_from_the_manual_entry(self):
        report = build_case_report(run_dir=self.run_dir, llm_usage_path=self.usage,
                                   policy=self.policy)
        self.assertEqual(report["llm"]["input_tokens"], 54000)
        self.assertEqual(report["llm"]["cache_tokens"], 12000)

    def test_a_shared_case_id_makes_the_runs_comparable(self):
        report = build_case_report(run_dir=self.run_dir, llm_usage_path=self.usage,
                                   policy=self.policy)
        self.assertEqual(report["comparability_reasons"], [])

    def test_a_different_policy_version_makes_them_incomparable(self):
        payload = llm_usage()
        payload["identity"]["policy_version"] = "text-quality-prefilter-v2"
        self.usage.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        report = build_case_report(run_dir=self.run_dir, llm_usage_path=self.usage,
                                   policy=self.policy)
        self.assertFalse(report["comparable"])
        self.assertTrue(any("policy_version" in reason
                            for reason in report["comparability_reasons"]))

    def test_the_markdown_warns_loudly_when_runs_are_not_comparable(self):
        # The Jev-side identity is derived from the manual entry, so the only
        # way to make the two sides disagree is to let the run directory name
        # a different case: perturbing a declared-only field would leave the
        # report comparable and this test would pass without proving anything.
        foreign = "sha256:" + "1" * 64
        for operation, overrides in (
            ("text_quality_prefilter", {}),
            ("knowledge_relevance", {"input_tokens": 4000, "output_tokens": 300,
                                     "request_count": 2, "elapsed_ms": 200,
                                     "estimated_cost_usd": "0.000168000000"}),
        ):
            write_atomic(
                self.run_dir / "jev" / operation / "trace" / "0001.json",
                trace(operation, benchmark_case_id=foreign, **overrides),
            )
        text = report_to_markdown(build_case_report(
            run_dir=self.run_dir, llm_usage_path=self.usage, policy=self.policy
        ))
        # Pin the banner and the reason list themselves. A looser assertion on
        # "不可比较" also matches the calibration line, so it would stay green
        # with the banner deleted.
        self.assertIn("**两次运行不可比较，下列数值仅供排查，不得作为结论。**", text)
        self.assertIn("不可比较原因：", text)
        self.assertIn("benchmark_case_id differs", text)

    def test_the_report_says_which_identity_fields_the_run_attested(self):
        report = build_case_report(run_dir=self.run_dir, llm_usage_path=self.usage,
                                   policy=self.policy)
        attestation = report["identity_attestation"]
        self.assertEqual(attestation["verified"],
                         ["benchmark_case_id", "policy_version"])
        self.assertEqual(attestation["declared_only"],
                         ["input_revision_vector_sha256", "draft_sha256",
                          "rule_version", "artifact_params"])

    def test_a_waiting_trace_is_excluded_from_the_speed_sample(self):
        write_atomic(
            self.run_dir / "jev" / "knowledge_relevance" / "trace" / "0002.json",
            trace("knowledge_relevance", status="waiting_for_jev_key",
                  input_tokens=None, output_tokens=None, estimated_cost_usd=None,
                  elapsed_ms=600_000, request_count=0),
        )
        report = build_case_report(run_dir=self.run_dir, llm_usage_path=self.usage,
                                   policy=self.policy)
        self.assertEqual(report["jev_assisted"]["elapsed_ms"], 600)

    def test_the_calibration_suggestion_never_flips_the_status_itself(self):
        report = build_case_report(run_dir=self.run_dir, llm_usage_path=self.usage,
                                   policy=self.policy)
        for entry in report["calibration"]:
            with self.subTest(operation=entry["operation"]):
                self.assertEqual(entry["current"], "experimental")
                self.assertIn(entry["suggestion"],
                              ("keep_experimental", "review_thresholds",
                               "eligible_for_calibrated"))
        self.assertEqual(
            self.policy["operations"]["text_quality_prefilter"]["calibration_status"],
            "experimental",
        )

    def test_printing_only_writes_nothing(self):
        code, out, _ = _run_cli([
            "--run-dir", str(self.run_dir), "--llm-usage", str(self.usage),
            "--policy", str(default_policy_path()),
        ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["schema_version"], COMPARISON_SCHEMA)
        self.assertFalse(list(self.root.rglob("*-audit")))

    def test_exporting_writes_the_report_outside_the_plugin(self):
        exports = self.root / "exports"
        exports.mkdir()
        code, _, _ = _run_cli([
            "--run-dir", str(self.run_dir), "--llm-usage", str(self.usage),
            "--output-dir", str(exports), "--plugin-root", str(self.plugin),
            "--policy", str(default_policy_path()),
        ])
        self.assertEqual(code, 0)
        bundle = exports / "path-comparison-audit"
        self.assertTrue((bundle / "comparison.json").is_file())
        self.assertTrue((bundle / "comparison.md").is_file())
        self.assertIn("路径对比",
                      (bundle / "comparison.md").read_text(encoding="utf-8"))

    def test_exporting_into_the_plugin_is_refused(self):
        code, _, err = _run_cli([
            "--run-dir", str(self.run_dir), "--llm-usage", str(self.usage),
            "--output-dir", str(self.plugin), "--plugin-root", str(self.plugin),
            "--policy", str(default_policy_path()),
        ])
        self.assertEqual(code, 1)
        self.assertIn("outside the plugin", err)

    def test_a_database_sourced_usage_entry_is_refused(self):
        payload = llm_usage(source="cc-switch-sqlite:/home/u/.cc-switch.db")
        self.usage.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        code, _, err = _run_cli([
            "--run-dir", str(self.run_dir), "--llm-usage", str(self.usage),
            "--policy", str(default_policy_path()),
        ])
        self.assertEqual(code, 1)
        self.assertIn("manual", err)


def _run_cli(argv):
    import io

    stdout, stderr = io.StringIO(), io.StringIO()
    code = compare_main(argv, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


if __name__ == "__main__":
    unittest.main()
