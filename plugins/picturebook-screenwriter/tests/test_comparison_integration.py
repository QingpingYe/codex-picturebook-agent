"""Phase 4 integration: the comparison report and its export boundary."""

import copy
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
from comparison import (  # noqa: E402
    COMPARISON_SCHEMA,
    build_case_report,
    load_traces,
    report_to_markdown,
)
from decision_contract import default_policy_path, load_policy  # noqa: E402
from jev_client import (  # noqa: E402
    API_KEY_ENV,
    FakeTransport,
    JevClient,
    TransportResponse,
)
from jev_runner import RunnerConfig, resume_operation, write_atomic  # noqa: E402
from page_quality import parse_script_pages  # noqa: E402
from recall import partition  # noqa: E402
from redline_catalog import RedlineRule, catalog_from_bundle  # noqa: E402
from relevance import screen_candidates  # noqa: E402
from required_marking import mark_bundle  # noqa: E402
from screening_runner import (  # noqa: E402
    plan_batches,
    run_screening,
    screening_items,
)

CASE_ID = "sha256:" + "0" * 64
RUN_ID = "20260923-integration-0001"


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

    def test_the_jev_column_does_not_invent_a_cache_measurement(self):
        # The trace contract carries no cache-token field, so the Jev column has
        # nothing to report for it: a hard-coded 0 in the product reads as "this
        # run was measured and used no cache", which nobody measured.
        report = build_case_report(run_dir=self.run_dir, llm_usage_path=self.usage,
                                   policy=self.policy)
        self.assertIsNone(report["jev_assisted"]["cache_tokens"])
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


# --- what the report reads out of a run that really happened ----------------

SCRIPT = """# 学会分享

| # | 页码 | Text | 插图 |
| --- | --- | --- | --- |
| 1 | 1 | Miles found a red ball. / He held it tight. | 男孩抱着红球 |
| 2 | 2 | "Mine!" he said. | 男孩转身 |
| 3 | 3 | Miles rolled the ball to her. / "Let's play!" | 两人一起玩 |
"""

CORRECTIONS_BODY = """# 纠正台账

## 强制性禁止条目

绝不可把解决问题的方式写成"变勇敢了"。

## 红线机器可读块

<!-- machine-data: redline_terms -->
```yaml
redline_terms:
  - "变勇敢了"
  - "魔法解决一切"
```
"""

WORLDVIEW_BODY = """# 世界观总纲

## 核心价值主张

勇气不是不害怕，而是害怕时仍然向前。

## 创作红线不变量

- 迈尔斯不能飞行。

## 场景清单

| 场景 | 说明 |
| --- | --- |
| 森林 | 迈尔斯家附近 |
"""

REFERENCES_BODY = """# 参考资料

## 参考书目

- 《森林的故事》

## 备选素材

- 树屋草图
"""

PAGES = parse_script_pages(SCRIPT)
RELEVANCE_QUESTIONS = ("relevant", "usable_evidence",
                       "contradicts_task_assumption", "instruction_like_content")
REDLINE_RULES = (
    RedlineRule("redline-aaa", "变勇敢了", "禁止直接写成变勇敢了",
                "海外绘本/小老鼠迈尔斯/corrections", 17),
    RedlineRule("redline-bbb", "魔法解决一切", "禁止用魔法解决冲突",
                "海外绘本/小老鼠迈尔斯/corrections", 17),
)


def noul(value):
    return {"type": "noul", "noul": value}


def unbandable_answer():
    """A contract-valid answer the router refuses to band.

    `routing.answer_band` only bands `noul` answers, so a Choice for a noul
    question satisfies the result contract and then makes the router refuse the
    item. That is the reachable shape behind "a dimension nobody could read":
    the answer is not malformed, it just carries no yes/no likelihood the
    policy's bands were written against.
    """

    return {
        "type": "choice", "choice": "是的",
        "probabilities": {"是的": 0.9, "不是": 0.1}, "confidence": 0.9,
    }


def authority_bundle():
    return {
        "items": ({"key": "海外绘本/小老鼠迈尔斯/corrections",
                   "doc_token": "doxcnExample", "revision_id": 17,
                   "title": "海外绘本/小老鼠迈尔斯/corrections",
                   "content": CORRECTIONS_BODY, "source_revisions": {"node-a": "17"},
                   "status": "published", "index_synced": True},),
        "warnings": (), "offline": False, "fetched_at": "2026-09-23T10:30:00+08:00",
    }


KNOWLEDGE_BUNDLE = {
    "items": tuple(
        {
            "key": key, "doc_token": f"doxcn{index}", "revision_id": 17,
            "title": key, "content": body, "source_revisions": {"node-a": "17"},
            "status": "published", "index_synced": True,
        }
        for index, (key, body) in enumerate((
            ("海外绘本/小老鼠迈尔斯/worldview", WORLDVIEW_BODY),
            ("海外绘本/小老鼠迈尔斯/references", REFERENCES_BODY),
        ))
    ),
    "warnings": (), "offline": False, "fetched_at": "2026-09-23T10:30:00+08:00",
}


def clear_answers_for(items):
    """Exactly the question set these items ask, every answer in the clear band.

    Supplying an answer for a question that was not asked is rejected as an
    unknown answer id, so this mirrors `screening_items` rather than guessing.
    """

    return {
        f"{item['item_id']}::{dimension}": noul(0.05)
        for item in items
        for dimension in item["dimensions"]
    }


def all_clear_answers():
    """The clear-band answer set for every page window this script asks about."""

    return clear_answers_for(screening_items(PAGES, REDLINE_RULES))


def knowledge_candidates():
    """The soft chunks the relevance operation will really ask about."""

    _always_kept, candidates = partition(
        mark_bundle(KNOWLEDGE_BUNDLE), artifact_type="script"
    )
    return candidates


class RealRunComparisonTests(unittest.TestCase):
    """The report reads counts from a run that really happened.

    `trace_for` used to write a zero pair for every attempt, so the comparison
    report's escalation rate was structurally 0% and its calibration advice
    could never say anything but "promote". The repair is the `verdicts=` hook
    the two operations hand the shared runner; these tests drive the shipped
    screening paths against a scripted transport and hold the trace, the
    operation's own summary and the report to one another.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.policy = load_policy(default_policy_path())

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _client(answers):
        return RealRunComparisonTests._client_bodies([
            RealRunComparisonTests._response(answers),
        ])

    @staticmethod
    def _response(answers, status_code=200):
        body = "" if status_code != 200 else json.dumps(
            {"model": "jev-1.13.0", "answers": answers,
             "usage": {"input_tokens": 400, "output_tokens": 40}},
            ensure_ascii=False,
        )
        return TransportResponse(status_code, body, {})

    @staticmethod
    def _client_bodies(responses):
        return JevClient(
            FakeTransport(responses=list(responses)),
            environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None,
        )

    @staticmethod
    def _summed(run_dir, key):
        return sum(trace.get(key) or 0 for trace in load_traces(run_dir))

    def _usage_path(self, trace, operation):
        """A usage entry for the run the trace belongs to, as a user would copy it."""

        payload = {
            "identity": {
                "benchmark_case_id": trace["benchmark_case_id"],
                "input_revision_vector_sha256": "a" * 64,
                "draft_sha256": "b" * 64,
                "policy_version": self.policy["operations"][operation]["policy_version"],
                "rule_version": "text-quality-prefilter-rules-v1",
                "artifact_params": {"age_band": "3-6", "genre": "温情",
                                    "page_count": 3},
            },
            "model_label": "plain-llm", "source": "cc-switch-manual-entry",
            "elapsed_ms": 180000, "request_count": 12, "input_tokens": 54000,
            "output_tokens": 9000, "estimated_cost_usd": "1.234500000000",
        }
        path = self.root / "llm-usage.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def _screen_pages(self, answers):
        run_dir = self.root / "quality-run"
        outcome = run_screening(
            run_id=RUN_ID, policy=self.policy, pages=PAGES, rules=REDLINE_RULES,
            bundle=authority_bundle(),
            config=RunnerConfig(run_dir=run_dir, policy=self.policy),
            client=self._client(answers), age_band="3-6",
        )
        return run_dir, outcome

    def _screen_knowledge(self, overrides=None):
        run_dir = self.root / "knowledge-run"
        answers = {
            f"{chunk.chunk_id}::{question_id}": noul(0.02)
            for chunk in knowledge_candidates()
            for question_id in RELEVANCE_QUESTIONS
        }
        answers.update(overrides or {})
        outcome = screen_candidates(
            run_id=RUN_ID, policy=self.policy, artifact_type="script",
            task_description="起草第 5 页", brief="分享主题，3-6 岁",
            bundle=KNOWLEDGE_BUNDLE,
            config=RunnerConfig(run_dir=run_dir, policy=self.policy),
            client=self._client(answers),
        )
        return run_dir, outcome

    def test_the_traces_carry_the_counts_the_screen_decided(self):
        rules = tuple(rule.as_triple() for rule in REDLINE_RULES)
        answers = all_clear_answers()
        risky = f"page-2::redline:{REDLINE_RULES[0].rule_id}"
        # The runner has to ask exactly this id, so answering it is also a
        # check that the catalog's id and the policy's pattern still agree.
        self.assertIn(risky, answers)
        answers[risky] = noul(0.93)
        run_dir, outcome = self._screen_pages(answers)
        self.assertTrue(load_traces(run_dir))
        self.assertEqual(self._summed(run_dir, "screened_clear_count"),
                         outcome.summary["screened_clear"])
        self.assertEqual(
            self._summed(run_dir, "escalated_count"),
            outcome.summary["escalated"] + outcome.summary["runtime_failure"],
        )
        self.assertEqual(
            self._summed(run_dir, "screened_clear_count")
            + self._summed(run_dir, "escalated_count"),
            outcome.summary["total"],
        )
        self.assertEqual(self._summed(run_dir, "escalated_count"), 1)

    def test_the_report_shows_the_escalation_rate_the_run_decided(self):
        # Every dimension risky. The report has to read "review the thresholds";
        # with the zero pair this used to record, the same run read as 0%
        # escalation and an operation ready to be promoted.
        run_dir, outcome = self._screen_pages(
            {key: noul(0.93) for key in all_clear_answers()}
        )
        trace = load_traces(run_dir)[0]
        self.assertEqual(self._summed(run_dir, "escalated_count"),
                         outcome.summary["total"])
        report = build_case_report(
            run_dir=run_dir, policy=self.policy,
            llm_usage_path=self._usage_path(trace, "text_quality_prefilter"),
        )
        self.assertTrue(report["comparable"])
        self.assertEqual(report["jev_assisted"]["quality_items_entered"],
                         outcome.summary["total"])
        self.assertEqual(report["jev_assisted"]["issues_found"],
                         outcome.summary["total"])
        self.assertIsNone(report["jev_assisted"]["knowledge_items_entered"])
        advice = next(entry for entry in report["calibration"]
                      if entry["operation"] == "text_quality_prefilter")
        self.assertEqual(advice["suggestion"], "review_thresholds")

    def test_a_screen_that_cleared_everything_reports_no_issue(self):
        run_dir, outcome = self._screen_pages(all_clear_answers())
        trace = load_traces(run_dir)[0]
        self.assertEqual(self._summed(run_dir, "screened_clear_count"),
                         outcome.summary["total"])
        self.assertEqual(self._summed(run_dir, "escalated_count"), 0)
        report = build_case_report(
            run_dir=run_dir, policy=self.policy,
            llm_usage_path=self._usage_path(trace, "text_quality_prefilter"),
        )
        self.assertEqual(report["jev_assisted"]["issues_found"], 0)
        self.assertEqual(report["jev_assisted"]["quality_items_entered"], 0)
        advice = next(entry for entry in report["calibration"]
                      if entry["operation"] == "text_quality_prefilter")
        self.assertEqual(advice["suggestion"], "eligible_for_calibrated")

    def test_a_clean_relevance_screen_hands_nothing_over(self):
        run_dir, outcome = self._screen_knowledge()
        trace = load_traces(run_dir)[0]
        self.assertEqual(trace["operation"], "knowledge_relevance")
        self.assertEqual(self._summed(run_dir, "escalated_count"), 0)
        self.assertEqual(self._summed(run_dir, "screened_clear_count"),
                         len(outcome.routes))
        report = build_case_report(
            run_dir=run_dir, policy=self.policy,
            llm_usage_path=self._usage_path(trace, "knowledge_relevance"),
        )
        self.assertEqual(report["jev_assisted"]["knowledge_items_entered"], 0)
        self.assertIsNone(report["jev_assisted"]["quality_items_entered"])

    def test_a_conflicting_chunk_lands_on_the_knowledge_row(self):
        conflict = knowledge_candidates()[0]
        run_dir, outcome = self._screen_knowledge({
            f"{conflict.chunk_id}::contradicts_task_assumption": noul(0.9),
        })
        trace = load_traces(run_dir)[0]
        self.assertEqual([chunk.chunk_id for chunk in outcome.conflicts],
                         [conflict.chunk_id])
        self.assertEqual(self._summed(run_dir, "escalated_count"), 1)
        report = build_case_report(
            run_dir=run_dir, policy=self.policy,
            llm_usage_path=self._usage_path(trace, "knowledge_relevance"),
        )
        self.assertEqual(report["jev_assisted"]["knowledge_items_entered"], 1)
        self.assertIsNone(report["jev_assisted"]["quality_items_entered"])
        self.assertEqual(report["jev_assisted"]["issues_found"], 1)

    def test_a_dimension_the_router_cannot_read_is_escalated_never_cleared(self):
        # The answer passes the result contract and cannot be banded, so the
        # dimension becomes a runtime failure: the plain LLM has to look at it.
        # Counting it as cleared would shrink the escalation share, and that is
        # the one number the calibration advice is made of.
        unreadable = "page-1::direct_moralizing"
        answers = all_clear_answers()
        self.assertIn(unreadable, answers)
        answers[unreadable] = unbandable_answer()
        run_dir, outcome = self._screen_pages(answers)
        self.assertEqual(outcome.summary["runtime_failure"], 1)
        self.assertEqual(self._summed(run_dir, "escalated_count"), 1)
        self.assertEqual(
            self._summed(run_dir, "screened_clear_count"),
            outcome.summary["total"] - 1,
        )

    def test_a_chunk_the_router_cannot_read_is_handed_to_the_llm(self):
        # Same shape on the knowledge side: a chunk whose answers cannot be
        # read is kept and flagged, never silently excluded, so its verdict is
        # an escalation and the trace says so.
        chunk = knowledge_candidates()[0]
        run_dir, outcome = self._screen_knowledge({
            f"{chunk.chunk_id}::relevant": unbandable_answer(),
        })
        self.assertIn(chunk, outcome.kept)
        self.assertIn(chunk, outcome.uncertain)
        self.assertEqual(self._summed(run_dir, "escalated_count"), 1)
        self.assertEqual(
            self._summed(run_dir, "screened_clear_count"), len(outcome.routes)
        )
        self.assertEqual(
            self._summed(run_dir, "screened_clear_count")
            + self._summed(run_dir, "escalated_count"),
            len(knowledge_candidates()),
        )

    def test_a_batch_resumed_after_the_key_was_refused_is_named(self):
        # The documented recovery from a credential wait is `jev_runner
        # resume`, which has no operation routing to hand the runner: the batch
        # it settles writes the zero pair. The report may not read that as
        # "this operation cleared everything" — the two counts cover only the
        # batches that recorded theirs, so the share may be off either way — so
        # the advice has to withhold the promotion and say why.
        policy = copy.deepcopy(self.policy)
        policy["operations"]["text_quality_prefilter"]["max_items_per_request"] = 1
        batches = plan_batches(screening_items(PAGES, REDLINE_RULES), 1)
        self.assertEqual(len(batches), 3)
        # One batch asks one window's questions: an answer set for the whole
        # draft is rejected as a response to a single-batch request.
        answers = [clear_answers_for(batch) for batch in batches]
        run_dir = self.root / "quality-run"
        config = RunnerConfig(run_dir=run_dir, policy=policy)
        outcome = run_screening(
            run_id=RUN_ID, policy=policy, pages=PAGES, rules=REDLINE_RULES,
            bundle=authority_bundle(), config=config, age_band="3-6",
            client=self._client_bodies([
                self._response(answers[0]), self._response(answers[1]),
                # The third call comes back refused: the key the user had is
                # no longer accepted, which leaves the run waiting and
                # resumable with exactly one open call.
                self._response(answers[2], status_code=401),
            ]),
        )
        self.assertEqual(
            [result["status"] for result in outcome.results],
            ["succeeded", "succeeded", "waiting_for_jev_key"],
        )
        pending = next((run_dir / "jev").glob("*/pending.json"))
        request = json.loads(
            (pending.parent / "request.json").read_text(encoding="utf-8")
        )
        resumed = resume_operation(
            config, self._client(answers[2]), request["context_refs"]
        )
        self.assertEqual(resumed["status"], "succeeded")
        traces = load_traces(run_dir)
        # The wait wrote no trace of its own, so the resumed attempt is the
        # only record of that batch — and it carries the zero pair.
        self.assertEqual(len(traces), 3)
        self.assertEqual(self._summed(run_dir, "escalated_count"), 0)
        report = build_case_report(
            run_dir=run_dir, policy=policy,
            llm_usage_path=self._usage_path(traces[0], "text_quality_prefilter"),
        )
        measured = report["jev_operations"][0]
        self.assertEqual(measured["traces"], 3)
        self.assertEqual(measured["traces_without_verdicts"], 1)
        self.assertIn("traces_without_verdicts=1", report["jev_assisted"]["notes"])
        self.assertIn("traces_without_verdicts=1", report_to_markdown(report))
        advice = next(entry for entry in report["calibration"]
                      if entry["operation"] == "text_quality_prefilter")
        self.assertEqual(advice["suggestion"], "keep_experimental")
        self.assertIn("没有记下判定计数", advice["reason"])


if __name__ == "__main__":
    unittest.main()
