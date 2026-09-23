import unittest

from routing import (
    RoutingError,
    answer_band,
    condition_matches,
    probability_band,
    route_item,
    route_items,
    rule_matches,
)

BANDS = {"clear_at_or_below": "0.25", "risk_at_or_above": "0.70"}


def noul(value):
    return {"type": "noul", "noul": value}


class BandTests(unittest.TestCase):
    def test_bands_split_at_the_declared_boundaries(self):
        self.assertEqual(probability_band(0.0, BANDS), "clear")
        self.assertEqual(probability_band(0.25, BANDS), "clear")
        self.assertEqual(probability_band(0.5, BANDS), "grey")
        self.assertEqual(probability_band(0.7, BANDS), "risk")
        self.assertEqual(probability_band(1.0, BANDS), "risk")

    def test_bands_use_decimal_not_binary_float(self):
        # 0.35 is not exactly representable in binary floating point.
        bands = {"clear_at_or_below": "0.35", "risk_at_or_above": "0.70"}
        self.assertEqual(probability_band(0.35, bands), "clear")

    def test_a_non_noul_answer_cannot_be_banded(self):
        with self.assertRaises(RoutingError):
            answer_band({"type": "score", "score": 1.05}, BANDS)
        with self.assertRaises(RoutingError):
            answer_band({"type": "choice", "choice": "a"}, BANDS)


class ConditionTests(unittest.TestCase):
    def test_a_matching_band_satisfies_the_condition(self):
        condition = {"question_id": "relevant", "bands": ["risk"]}
        self.assertTrue(condition_matches(condition, {"relevant": noul(0.9)}, BANDS))

    def test_a_different_band_does_not_satisfy_the_condition(self):
        condition = {"question_id": "relevant", "bands": ["risk"]}
        self.assertFalse(condition_matches(condition, {"relevant": noul(0.1)}, BANDS))

    def test_a_missing_answer_never_satisfies_a_condition(self):
        # Absence must not look like evidence, in either direction.
        risk = {"question_id": "relevant", "bands": ["risk"]}
        clear = {"question_id": "relevant", "bands": ["clear"]}
        self.assertFalse(condition_matches(risk, {}, BANDS))
        self.assertFalse(condition_matches(clear, {}, BANDS))


class RuleTests(unittest.TestCase):
    def test_any_of_needs_one_match(self):
        rule = {"route": "escalate_llm", "any_of": [
            {"question_id": "a", "bands": ["risk"]},
            {"question_id": "b", "bands": ["risk"]},
        ]}
        self.assertTrue(rule_matches(rule, {"a": noul(0.1), "b": noul(0.9)}, BANDS))
        self.assertFalse(rule_matches(rule, {"a": noul(0.1), "b": noul(0.1)}, BANDS))

    def test_all_of_needs_every_match(self):
        rule = {"route": "exclude_soft", "all_of": [
            {"question_id": "a", "bands": ["clear"]},
            {"question_id": "b", "bands": ["clear"]},
        ]}
        self.assertTrue(rule_matches(rule, {"a": noul(0.1), "b": noul(0.1)}, BANDS))
        self.assertFalse(rule_matches(rule, {"a": noul(0.1), "b": noul(0.9)}, BANDS))

    def test_all_of_skips_questions_that_were_not_asked(self):
        # An item exempt from one dimension must still be clearable: the
        # unasked condition is skipped rather than counted as a failure.
        rule = {"route": "screened_clear", "all_of": [
            {"question_id": "a", "bands": ["clear"]},
            {"question_id": "exempt", "bands": ["clear"]},
        ]}
        self.assertTrue(rule_matches(rule, {"a": noul(0.1)}, BANDS))

    def test_all_of_that_evaluated_nothing_never_matches(self):
        # Otherwise a wholly missing answer set would read as a clear verdict.
        rule = {"route": "screened_clear", "all_of": [
            {"question_id": "a", "bands": ["clear"]},
        ]}
        self.assertFalse(rule_matches(rule, {}, BANDS))

    def test_an_unanswered_condition_blocks_a_rule_that_withholds_content(self):
        # Skipping an unasked question is only sound while the rule cannot drop
        # content: nothing may be excluded on evidence that was never given.
        rule = {"route": "exclude_soft", "all_of": [
            {"question_id": "a", "bands": ["clear"]},
            {"question_id": "never_asked", "bands": ["clear"]},
        ]}
        self.assertFalse(rule_matches(rule, {"a": noul(0.1)}, BANDS))


def operation_policy(**overrides):
    policy = {
        "policy_version": "knowledge-relevance-v1",
        "calibration_status": "experimental",
        "fallback_route": "include",
        "fallback_label": "uncertain",
        "question_templates": {
            "relevant": {"type": "noul", "instructions": "是否相关？"},
            "contradicts": {"type": "noul", "instructions": "是否反驳？"},
        },
        "routing": {
            "bands": BANDS,
            "rules": [
                {"route": "escalate_llm", "label": "conflict",
                 "any_of": [{"question_id": "contradicts", "bands": ["risk"]}]},
                {"route": "exclude_soft", "label": "clearly_irrelevant",
                 "all_of": [{"question_id": "relevant", "bands": ["clear"]},
                            {"question_id": "contradicts", "bands": ["clear"]}]},
            ],
        },
    }
    policy.update(overrides)
    return policy


class RouteItemTests(unittest.TestCase):
    def test_the_first_matching_rule_wins(self):
        entry = route_item("k#001", {"relevant": noul(0.1), "contradicts": noul(0.9)},
                           operation_policy())
        self.assertEqual(entry, {"item_id": "k#001", "route": "escalate_llm",
                                 "label": "conflict"})

    def test_a_later_rule_matches_when_the_first_does_not(self):
        entry = route_item("k#002", {"relevant": noul(0.1), "contradicts": noul(0.1)},
                           operation_policy())
        self.assertEqual(entry["route"], "exclude_soft")
        self.assertEqual(entry["label"], "clearly_irrelevant")

    def test_no_rule_match_falls_back_conservatively(self):
        entry = route_item("k#003", {"relevant": noul(0.5), "contradicts": noul(0.5)},
                           operation_policy())
        self.assertEqual(entry, {"item_id": "k#003", "route": "include",
                                 "label": "uncertain"})

    def test_a_missing_answer_falls_back_instead_of_excluding(self):
        entry = route_item("k#004", {"relevant": noul(0.1)}, operation_policy())
        self.assertEqual(entry["route"], "include")

    def test_route_items_preserves_item_order(self):
        routes = route_items(
            {"k#001": {"relevant": noul(0.1), "contradicts": noul(0.1)},
             "k#002": {"relevant": noul(0.9), "contradicts": noul(0.1)}},
            operation_policy(),
        )
        self.assertEqual([entry["item_id"] for entry in routes], ["k#001", "k#002"])
        self.assertEqual([entry["route"] for entry in routes], ["exclude_soft", "include"])

    def test_a_missing_bands_object_is_refused(self):
        with self.assertRaises(RoutingError):
            route_item("k#001", {"relevant": noul(0.1)},
                       operation_policy(routing={"rules": []}))


if __name__ == "__main__":
    unittest.main()
