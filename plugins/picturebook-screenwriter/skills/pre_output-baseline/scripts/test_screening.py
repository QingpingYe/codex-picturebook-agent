import unittest

from screening import (
    SCREENING_OUTCOMES,
    ScreeningDecision,
    cleared_items,
    escalated_items,
    failed_items,
    failure_decision,
    may_skip_llm_review,
    screening_decision,
    summarise,
)

DIMENSIONS = ("direct_moralizing", "read_aloud_friction")

BANDS = {"clear_at_or_below": "0.25", "risk_at_or_above": "0.70"}


def operation_policy(fallback="escalate_llm"):
    return {
        "policy_version": "text-quality-prefilter-v1",
        "calibration_status": "experimental",
        "fallback_route": fallback,
        "fallback_label": "uncertain",
        "question_templates": {
            "direct_moralizing": {"type": "noul", "instructions": "是否说教？"},
            "read_aloud_friction": {"type": "noul", "instructions": "是否拗口？"},
        },
        "routing": {
            "bands": BANDS,
            "rules": [
                {"route": "escalate_llm", "label": "risk",
                 "any_of": [{"question_id": "direct_moralizing", "bands": ["risk"]}]},
                {"route": "screened_clear", "label": "clear",
                 "all_of": [{"question_id": "direct_moralizing", "bands": ["clear"]},
                            {"question_id": "read_aloud_friction", "bands": ["clear"]}]},
            ],
        },
    }


def noul(value):
    return {"type": "noul", "noul": value}


def answers(**values):
    return {name: noul(value) for name, value in values.items()}


class ConstructionTests(unittest.TestCase):
    def test_a_clear_item_is_screened_clear(self):
        decision = screening_decision(
            item_id="page-05::direct_moralizing", dimension="direct_moralizing",
            answers=answers(direct_moralizing=0.05, read_aloud_friction=0.05),
            operation_policy=operation_policy(),
        )
        self.assertEqual(decision.outcome, "screened_clear")
        self.assertEqual(decision.label, "clear")

    def test_a_risky_item_escalates(self):
        decision = screening_decision(
            item_id="page-05::direct_moralizing", dimension="direct_moralizing",
            answers=answers(direct_moralizing=0.9, read_aloud_friction=0.05),
            operation_policy=operation_policy(),
        )
        self.assertEqual(decision.outcome, "escalate_llm")

    def test_a_grey_item_escalates_via_the_fallback(self):
        decision = screening_decision(
            item_id="page-05::direct_moralizing", dimension="direct_moralizing",
            answers=answers(direct_moralizing=0.5, read_aloud_friction=0.5),
            operation_policy=operation_policy(),
        )
        self.assertEqual(decision.outcome, "escalate_llm")
        self.assertEqual(decision.label, "uncertain")

    def test_a_clear_verdict_covers_only_the_dimensions_that_answered(self):
        # Routing skips a question the item never asked, and that skipping is
        # load bearing: it is what lets a closing page clear while its
        # page-turn question is not asked (Task 5's clear rule covers all five
        # dimensions). So a clear verdict is written for the answers that came
        # back, and an omitted dimension is not silently covered by it — the
        # `screened_clear` never claims anything about the questions it did not
        # see. The fail-closed guard for an answer the model *was* asked for and
        # skipped lives one level up: `validate_answer_ids` settles that
        # response as `failed`/`incomplete_response`, and the runner turns a
        # non-succeeded batch into a runtime failure for each of its dimensions.
        decision = screening_decision(
            item_id="page-05::direct_moralizing", dimension="direct_moralizing",
            answers=answers(direct_moralizing=0.05),
            operation_policy=operation_policy(),
        )
        self.assertEqual(decision.outcome, "screened_clear")
        self.assertEqual(set(decision.probabilities), {"direct_moralizing"})

    def test_an_empty_answer_set_is_a_runtime_failure(self):
        decision = screening_decision(
            item_id="page-05::direct_moralizing", dimension="direct_moralizing",
            answers={}, operation_policy=operation_policy(),
        )
        self.assertEqual(decision.outcome, "runtime_failure")
        self.assertEqual(decision.reason, "no_answers")

    def test_a_broken_answer_shape_is_a_runtime_failure(self):
        decision = screening_decision(
            item_id="page-05::direct_moralizing", dimension="direct_moralizing",
            answers={"direct_moralizing": {"type": "score", "score": 1.05}},
            operation_policy=operation_policy(),
        )
        self.assertEqual(decision.outcome, "runtime_failure")
        self.assertTrue(decision.reason.startswith("routing_error"))

    def test_failure_decisions_carry_no_probabilities(self):
        decision = failure_decision("page-05::x", "x", "timeout")
        self.assertEqual(decision.outcome, "runtime_failure")
        self.assertEqual(decision.probabilities, {})

    def test_probabilities_are_recorded_for_every_noul_answer(self):
        decision = screening_decision(
            item_id="page-05::direct_moralizing", dimension="direct_moralizing",
            answers=answers(direct_moralizing=0.05, read_aloud_friction=0.05),
            operation_policy=operation_policy(),
        )
        self.assertEqual(set(decision.probabilities),
                         {"direct_moralizing", "read_aloud_friction"})


class SkipGateTests(unittest.TestCase):
    def test_an_experimental_clear_never_skips_the_llm_review(self):
        decision = screening_decision(
            item_id="page-05::direct_moralizing", dimension="direct_moralizing",
            answers=answers(direct_moralizing=0.05, read_aloud_friction=0.05),
            operation_policy=operation_policy(),
        )
        self.assertEqual(decision.outcome, "screened_clear")
        self.assertFalse(may_skip_llm_review(decision, "experimental"))
        self.assertTrue(may_skip_llm_review(decision, "calibrated"))

    def test_an_escalated_item_never_skips_the_review(self):
        decision = screening_decision(
            item_id="page-05::direct_moralizing", dimension="direct_moralizing",
            answers=answers(direct_moralizing=0.9), operation_policy=operation_policy(),
        )
        self.assertFalse(may_skip_llm_review(decision, "calibrated"))

    def test_a_runtime_failure_never_skips_the_review(self):
        decision = failure_decision("page-05::x", "x", "timeout")
        self.assertFalse(may_skip_llm_review(decision, "calibrated"))


class SummaryTests(unittest.TestCase):
    def decisions(self):
        clear = screening_decision(
            item_id="a", dimension="direct_moralizing",
            answers=answers(direct_moralizing=0.05, read_aloud_friction=0.05),
            operation_policy=operation_policy(),
        )
        escalate = screening_decision(
            item_id="b", dimension="direct_moralizing",
            answers=answers(direct_moralizing=0.9), operation_policy=operation_policy(),
        )
        failed = failure_decision("c", "direct_moralizing", "timeout")
        return (clear, escalate, failed)

    def test_the_summary_counts_each_outcome(self):
        summary = summarise(self.decisions())
        self.assertEqual(summary["screened_clear"], 1)
        self.assertEqual(summary["escalated"], 1)
        self.assertEqual(summary["runtime_failure"], 1)
        self.assertEqual(summary["total"], 3)

    def test_the_summary_reports_grey_and_escalation_ratios(self):
        summary = summarise(self.decisions())
        self.assertAlmostEqual(summary["escalation_ratio"], 2 / 3)
        self.assertAlmostEqual(summary["screened_clear_ratio"], 1 / 3)

    def test_an_empty_decision_list_has_zero_ratios_not_a_division_error(self):
        summary = summarise(())
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["escalation_ratio"], 0.0)

    def test_selectors_partition_the_decisions(self):
        decisions = self.decisions()
        self.assertEqual([d.item_id for d in cleared_items(decisions)], ["a"])
        self.assertEqual([d.item_id for d in escalated_items(decisions)], ["b"])
        self.assertEqual([d.item_id for d in failed_items(decisions)], ["c"])

    def test_the_outcome_vocabulary_is_published(self):
        self.assertEqual(SCREENING_OUTCOMES,
                         ("screened_clear", "escalate_llm", "runtime_failure"))

    def test_every_decision_outcome_is_in_the_vocabulary(self):
        for decision in self.decisions():
            with self.subTest(item=decision.item_id):
                self.assertIn(decision.outcome, SCREENING_OUTCOMES)


if __name__ == "__main__":
    unittest.main()
