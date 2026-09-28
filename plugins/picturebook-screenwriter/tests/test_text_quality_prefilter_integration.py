"""Phase 3 integration: the text-quality pre-screen and blocking invariants.

Everything here drives the shipped modules end to end rather than a stub of
them, so the phase's guarantees are pinned where they are really used: the
red-line catalog is rebuilt from the authority pages, a literal scan is only a
candidate, a confirmed red line blocks and cannot be cleared afterwards, every
active red line is asked about even when the text does not literally match it,
and no failure of any kind -- an empty response, a missing answer, a refused
socket, an absent credential -- can be read as a clear verdict.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
for _relative in ("jev-decision-runtime", "knowledge-loader", "pre_output-baseline",
                  "craft-benchmark-check"):
    sys.path.insert(0, str(ROOT / "skills" / _relative / "scripts"))

from decision_contract import default_policy_path, load_policy  # noqa: E402
from jev_client import (  # noqa: E402
    API_KEY_ENV,
    FakeTransport,
    JevClient,
    JevTransportFailure,
    TransportResponse,
)
from jev_runner import RunnerConfig  # noqa: E402
from page_quality import parse_script_pages  # noqa: E402
from quality_gate import (  # noqa: E402
    AUTHORITY_SOURCE,
    PROXY_SOURCE,
    Finding,
    SemanticJudgment,
    build_report,
    resolve_findings,
)
from redline_catalog import RedlineRule, catalog_from_bundle  # noqa: E402
from redline_proxy import scan_redlines  # noqa: E402
from screening import cleared_items, may_skip_llm_review  # noqa: E402
from screening_runner import OPERATION, run_screening, screening_items  # noqa: E402

RUN_ID = "20260923-integration-0001"

SCRIPT = """# 学会分享

| # | 页码 | Text | 插图 |
| --- | --- | --- | --- |
| 1 | 1 | Miles found a red ball. / He held it tight. | 男孩抱着红球 |
| 2 | 2 | "Mine!" he said. | 男孩转身 |
| 3 | 3 | Miles rolled the ball to her. / "Let's play!" | 两人一起玩 |
"""

PAGES = parse_script_pages(SCRIPT)

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

# Two rules by hand for the routing and blocking tests. The catalog's own ids
# are exercised separately, because the policy matches the red-line family with
# a `redline:*` pattern while the catalog mints `redline-<sha12>`.
REDLINE_RULES = (
    RedlineRule("redline-aaa", "变勇敢了", "禁止直接写成变勇敢了",
                "海外绘本/小老鼠迈尔斯/corrections", 17),
    RedlineRule("redline-bbb", "魔法解决一切", "禁止用魔法解决冲突",
                "海外绘本/小老鼠迈尔斯/corrections", 17),
)


def bundle(body=CORRECTIONS_BODY):
    return {
        "items": ({"key": "海外绘本/小老鼠迈尔斯/corrections", "doc_token": "doxcnExample",
                   "revision_id": 17, "title": "海外绘本/小老鼠迈尔斯/corrections",
                   "content": body, "source_revisions": {"node-a": "17"},
                   "status": "published", "index_synced": True},),
        "warnings": (), "offline": False, "fetched_at": "2026-09-23T10:30:00+08:00",
    }


def noul(value):
    return {"type": "noul", "noul": value}


class Phase3IntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)
        self.policy = load_policy(default_policy_path())
        self.config = RunnerConfig(run_dir=self.run_dir, policy=self.policy)

    def tearDown(self):
        self._tmp.cleanup()

    def _client(self, answers, status_code=200):
        body = json.dumps({"model": "jev-1.13.0", "answers": answers,
                           "usage": {"input_tokens": 400, "output_tokens": 40}})
        return JevClient(FakeTransport(responses=[TransportResponse(status_code, body, {})]),
                         environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None)

    def _all_clear(self, rules=REDLINE_RULES):
        """Exactly the asked question set, every answer in the clear band.

        Supplying an answer for a question that was not asked is rejected as an
        unknown answer id, so this has to mirror `screening_items` precisely.
        """

        answers = {}
        for item in screening_items(PAGES, rules):
            for dimension in item["dimensions"]:
                answers[f"{item['item_id']}::{dimension}"] = noul(0.05)
        return answers

    def _run(self, answers, rules=REDLINE_RULES, proxy_findings=()):
        return run_screening(
            run_id=RUN_ID, policy=self.policy, pages=PAGES,
            rules=rules, bundle=bundle(), config=self.config,
            client=self._client(answers), age_band="3-6",
            proxy_findings=proxy_findings,
        )

    @staticmethod
    def _redline_question_id(rules, pattern, item_id="page-1"):
        """One question id built from the catalog's own rule ids.

        The policy addresses the red-line family as `redline:*` while
        `redline_catalog` mints `redline-<sha12>`: a test that hand-writes the
        id keeps passing after those two drift apart, which is exactly how the
        red-line half of the pre-screen once became pure overhead.
        """

        rule = next(rule for rule in rules if rule.pattern == pattern)
        return f"{item_id}::redline:{rule.rule_id}"

    def _decision(self, outcome, qualified):
        return next(
            decision for decision in outcome.decisions
            if decision.item_id == qualified
        )

    def test_the_catalog_comes_from_the_authority_machine_block(self):
        rules = catalog_from_bundle(bundle())
        self.assertEqual(sorted(rule.pattern for rule in rules),
                         ["变勇敢了", "魔法解决一切"])

    def test_a_literal_hit_is_a_candidate_and_does_not_block(self):
        rules = tuple(rule.as_triple() for rule in REDLINE_RULES)
        findings = scan_redlines("迈尔斯变勇敢了。", rules)
        self.assertTrue(findings)
        self.assertTrue(all(f.source == PROXY_SOURCE for f in findings))
        self.assertEqual(build_report(findings).status, "needs_user_decision")

    def test_only_a_confirmed_red_line_blocks(self):
        rules = tuple(rule.as_triple() for rule in REDLINE_RULES)
        findings = scan_redlines("迈尔斯变勇敢了。", rules)
        judgment = SemanticJudgment(findings[0].id, "FAIL", "无豁免语境", "第 2 页原文")
        resolved = resolve_findings(findings, [judgment])
        self.assertTrue(any(f.source == AUTHORITY_SOURCE for f in resolved))
        self.assertEqual(build_report(resolved).status, "blocked")

    def test_a_judgment_cannot_clear_a_promoted_red_line(self):
        confirmed = Finding("redline-aaa", AUTHORITY_SOURCE, "FAIL", "确认触犯", "依据")
        cleared = resolve_findings([confirmed], [SemanticJudgment(
            "redline-aaa", "PASS", "再想想好像没事", "")])
        self.assertEqual(build_report(cleared).status, "blocked")

    def test_every_active_red_line_is_screened_even_without_a_literal_hit(self):
        rules = tuple(rule.as_triple() for rule in REDLINE_RULES)
        self.assertEqual(scan_redlines("迈尔斯把球推了过去。", rules), ())
        outcome = self._run(self._all_clear())
        screened_rules = {
            decision.dimension for decision in outcome.decisions
            if decision.dimension.startswith("redline:")
        }
        self.assertEqual(screened_rules,
                         {f"redline:{rule.rule_id}" for rule in REDLINE_RULES})

    def test_a_catalog_red_line_clears_when_its_own_question_is_low(self):
        rules = catalog_from_bundle(bundle())
        qualified = self._redline_question_id(rules, "变勇敢了")
        answers = self._all_clear(rules)
        # The runner has to ask exactly this id: the assertion fails if the
        # catalog's id or the policy's `redline:*` pattern drifts.
        self.assertIn(qualified, answers)
        decision = self._decision(self._run(answers, rules=rules), qualified)
        self.assertEqual(decision.outcome, "screened_clear")

    def test_a_catalog_red_line_escalates_when_its_own_question_is_high(self):
        rules = catalog_from_bundle(bundle())
        qualified = self._redline_question_id(rules, "变勇敢了")
        answers = self._all_clear(rules)
        answers[qualified] = noul(0.93)
        outcome = self._run(answers, rules=rules)
        decision = self._decision(outcome, qualified)
        self.assertEqual(decision.outcome, "escalate_llm")
        self.assertEqual(decision.label, "risk")
        self.assertIn(qualified, {entry["item_id"] for entry in outcome.escalation_package})
        # One risky red line escalates that dimension and nothing else, so the
        # other red line on the same page stays clear on its own answer.
        other = self._redline_question_id(rules, "魔法解决一切")
        self.assertEqual(self._decision(outcome, other).outcome, "screened_clear")

    def test_a_failure_never_produces_a_clear_verdict(self):
        body = json.dumps({"model": "jev-1.13.0", "answers": {}, "usage": {}})
        client = JevClient(FakeTransport(responses=[TransportResponse(200, body, {})]),
                           environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None)
        outcome = run_screening(
            run_id=RUN_ID, policy=self.policy,
            pages=PAGES, rules=REDLINE_RULES, bundle=bundle(), config=self.config,
            client=client, age_band="3-6",
        )
        self.assertEqual(cleared_items(outcome.decisions), ())
        self.assertTrue(all(d.outcome == "runtime_failure" for d in outcome.decisions))
        self.assertTrue(all(d.reason == "failed:incomplete_response"
                            for d in outcome.decisions))

    def test_a_response_missing_one_asked_answer_fails_the_whole_batch(self):
        answers = self._all_clear()
        omitted = "page-2::emotion_told_not_shown"
        self.assertIn(omitted, answers)
        del answers[omitted]
        outcome = self._run(answers)
        self.assertEqual(cleared_items(outcome.decisions), ())
        self.assertTrue(all(d.outcome == "runtime_failure" for d in outcome.decisions))
        self.assertEqual(outcome.summary["runtime_failure"], outcome.summary["total"])
        # Neither the answered dimensions nor the omitted one may be routed:
        # the response was not a verdict, so no dimension of its batch is.
        self.assertIn(omitted, {d.item_id for d in outcome.decisions})

    def test_a_transport_failure_never_produces_a_clear_verdict(self):
        client = JevClient(FakeTransport(error=JevTransportFailure("refused")),
                           environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None)
        outcome = run_screening(
            run_id=RUN_ID, policy=self.policy,
            pages=PAGES, rules=REDLINE_RULES, bundle=bundle(), config=self.config,
            client=client, age_band="3-6",
        )
        self.assertEqual(cleared_items(outcome.decisions), ())
        self.assertTrue(all(d.outcome == "runtime_failure" for d in outcome.decisions))
        self.assertTrue(all(d.reason == "failed:JevTransportFailure"
                            for d in outcome.decisions))

    def test_a_missing_key_never_produces_a_clear_verdict(self):
        client = JevClient(FakeTransport(), environ={}, sleep=lambda _: None)
        outcome = run_screening(
            run_id=RUN_ID, policy=self.policy,
            pages=PAGES, rules=REDLINE_RULES, bundle=bundle(), config=self.config,
            client=client, age_band="3-6",
        )
        self.assertEqual(cleared_items(outcome.decisions), ())
        self.assertTrue(all(d.reason == "waiting_for_jev_key" for d in outcome.decisions))

    def test_the_experimental_policy_never_reduces_the_llm_review(self):
        outcome = self._run(self._all_clear())
        calibration = self.policy["operations"][OPERATION]["calibration_status"]
        self.assertEqual(calibration, "experimental")
        self.assertTrue(cleared_items(outcome.decisions))
        for decision in outcome.decisions:
            self.assertFalse(may_skip_llm_review(decision, calibration))

    def test_a_risky_dimension_reaches_the_escalation_package(self):
        answers = self._all_clear()
        answers["page-2::direct_moralizing"] = noul(0.97)
        outcome = self._run(answers)
        entry = next(e for e in outcome.escalation_package
                     if e["item_id"] == "page-2::direct_moralizing")
        self.assertEqual(entry["outcome"], "escalate_llm")
        self.assertIn("Mine!", entry["evidence"])

    def test_a_grey_dimension_reaches_the_escalation_package(self):
        answers = self._all_clear()
        answers["page-2::read_aloud_friction"] = noul(0.5)
        outcome = self._run(answers)
        self.assertIn("page-2::read_aloud_friction",
                      {e["item_id"] for e in outcome.escalation_package})

    def test_the_last_page_is_never_asked_about_page_turn_motivation(self):
        outcome = self._run(self._all_clear())
        asked = {decision.item_id for decision in outcome.decisions}
        self.assertNotIn("page-3::weak_page_turn_motivation", asked)
        self.assertIn("page-2::weak_page_turn_motivation", asked)

    def test_the_exempt_closing_page_still_clears_instead_of_failing(self):
        outcome = self._run(self._all_clear())
        closing = [d for d in outcome.decisions if d.item_id.startswith("page-3::")]
        self.assertTrue(closing)
        self.assertTrue(all(d.outcome == "screened_clear" for d in closing))
        self.assertEqual(outcome.summary["runtime_failure"], 0)

    def test_a_proxy_hit_that_was_not_escalated_is_reported_as_a_conflict(self):
        rules = tuple(rule.as_triple() for rule in REDLINE_RULES)
        findings = scan_redlines("迈尔斯变勇敢了。", rules)
        outcome = self._run(self._all_clear(), proxy_findings=findings)
        self.assertTrue(outcome.proxy_conflicts)
        self.assertEqual(outcome.proxy_conflicts[0]["reason"], "proxy_hit_not_escalated")

    def test_an_escalated_red_line_is_not_reported_as_a_conflict(self):
        rules = catalog_from_bundle(bundle())
        qualified = self._redline_question_id(rules, "变勇敢了")
        answers = self._all_clear(rules)
        answers[qualified] = noul(0.93)
        # The scan names the rule that matched, and the conflict check is about
        # that rule's dimension, so the scanned text only has to contain the
        # literal the catalog carries.
        findings = scan_redlines("迈尔斯变勇敢了。", tuple(r.as_triple() for r in rules))
        outcome = self._run(answers, rules=rules, proxy_findings=findings)
        self.assertTrue(findings)
        self.assertEqual(outcome.proxy_conflicts, ())

    def test_an_empty_catalog_is_reported_rather_than_silently_passing(self):
        outcome = self._run(self._all_clear(rules=()), rules=())
        self.assertEqual(outcome.catalog_size, 0)
        self.assertTrue(outcome.summary["catalog_gap"])
        self.assertTrue([d for d in outcome.decisions
                         if d.dimension == "direct_moralizing"])

    def test_the_summary_reports_the_ratios_the_acceptance_criteria_need(self):
        outcome = self._run(self._all_clear())
        for key in ("total", "screened_clear", "escalated", "runtime_failure",
                    "screened_clear_ratio", "escalation_ratio"):
            with self.subTest(key=key):
                self.assertIn(key, outcome.summary)


if __name__ == "__main__":
    unittest.main()
