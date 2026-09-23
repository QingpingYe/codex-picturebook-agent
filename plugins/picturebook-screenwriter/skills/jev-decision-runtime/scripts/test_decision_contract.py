import unittest

from decision_contract import (
    ContractError,
    OPERATIONS,
    POLICY_SCHEMA,
    REQUEST_SCHEMA,
    RESULT_SCHEMA,
    assert_no_credential_fields,
    default_policy_path,
    load_policy,
    operation_policy,
    validate_answer_ids,
    validate_policy,
    validate_request,
    validate_result,
)


def make_request(**overrides):
    request = {
        "schema_version": REQUEST_SCHEMA,
        "run_id": "20260923-example-0001",
        "operation": "knowledge_relevance",
        "model": "jev-1.13.0",
        "policy_version": "knowledge-relevance-v1",
        "state": "目标产物：一本 32 页、面向 3-6 岁的分享主题绘本。",
        "questions": {
            "relevant": {
                "type": "noul",
                "instructions": "该知识块是否直接影响当前产物？",
                "criteria": {"true": "直接影响", "false": "不影响"},
            }
        },
        "context_refs": [
            {
                "ref_id": "海外绘本/小老鼠迈尔斯/worldview",
                "kind": "knowledge_page",
                "revisions": {"node-a": "17"},
            }
        ],
        "benchmark_case_id": "sha256:" + "0" * 64,
    }
    request.update(overrides)
    return request


def make_result(**overrides):
    result = {
        "schema_version": RESULT_SCHEMA,
        "run_id": "20260923-example-0001",
        "operation": "knowledge_relevance",
        "provider": "typesafe",
        "requested_model": "jev-1.13.0",
        "resolved_model": "jev-1.13.0",
        "policy_version": "knowledge-relevance-v1",
        "answers": {"relevant": {"type": "noul", "noul": 0.91}},
        "routes": [],
        "usage": {"input_tokens": 296, "output_tokens": 20},
        "trace": {},
        "status": "succeeded",
    }
    result.update(overrides)
    return result


class RequestContractTests(unittest.TestCase):
    def test_valid_request_is_accepted(self):
        validate_request(make_request())

    def test_unknown_operation_is_rejected(self):
        with self.assertRaises(ContractError):
            validate_request(make_request(operation="write_the_story"))

    def test_moving_model_alias_is_rejected(self):
        for alias in ("jev-latest", "jev-preview"):
            with self.subTest(alias=alias), self.assertRaises(ContractError):
                validate_request(make_request(model=alias))

    def test_empty_questions_are_rejected(self):
        with self.assertRaises(ContractError):
            validate_request(make_request(questions={}))

    def test_empty_context_refs_are_rejected(self):
        with self.assertRaises(ContractError):
            validate_request(make_request(context_refs=[]))

    def test_all_operations_are_accepted(self):
        for operation in OPERATIONS:
            with self.subTest(operation=operation):
                validate_request(make_request(operation=operation))

    def test_credential_shaped_fields_are_rejected(self):
        broken = make_request()
        broken["api_key"] = "sk-should-never-be-here"
        with self.assertRaises(ContractError):
            validate_request(broken)

    def test_token_count_fields_are_not_mistaken_for_credentials(self):
        assert_no_credential_fields({"input_tokens": 1, "output_tokens": 2})


class ResultContractTests(unittest.TestCase):
    def test_valid_result_is_accepted(self):
        validate_result(make_result())

    def test_noul_must_be_a_probability(self):
        broken = make_result(answers={"relevant": {"type": "noul", "noul": 1.4}})
        with self.assertRaises(ContractError):
            validate_result(broken)

    def test_non_finite_probability_is_rejected(self):
        broken = make_result(answers={"relevant": {"type": "noul", "noul": float("nan")}})
        with self.assertRaises(ContractError):
            validate_result(broken)

    def test_choice_needs_matching_probabilities(self):
        broken = make_result(answers={"pick": {
            "type": "choice",
            "choice": "b",
            "probabilities": {"a": 1.0, "b": 0.0},
            "confidence": 1.0,
        }})
        with self.assertRaises(ContractError):
            validate_result(broken)

    def test_choice_probabilities_must_sum_to_one(self):
        broken = make_result(answers={"pick": {
            "type": "choice",
            "choice": "a",
            "probabilities": {"a": 0.4, "b": 0.4},
            "confidence": 0.1,
        }})
        with self.assertRaises(ContractError):
            validate_result(broken)

    def test_score_may_land_between_levels(self):
        validate_result(make_result(answers={"friction": {
            "type": "score",
            "score": 1.05,
            "legend": {"0": "顺口", "1": "略拗口", "2": "很拗口"},
            "probabilities": {"0": 0.0, "1": 0.95, "2": 0.05},
            "confidence": 0.92,
        }}))

    def test_waiting_result_must_not_carry_answers_or_trace(self):
        broken = make_result(status="waiting_for_jev_key")
        with self.assertRaises(ContractError):
            validate_result(broken)

    def test_waiting_result_with_empty_payload_is_accepted(self):
        validate_result(make_result(
            status="waiting_for_jev_key",
            answers={},
            routes=[],
            usage={"input_tokens": None, "output_tokens": None},
            trace={},
            resolved_model=None,
        ))

    def test_outcome_unknown_may_carry_a_trace(self):
        validate_result(make_result(
            status="outcome_unknown",
            answers={},
            routes=[],
            usage={"input_tokens": None, "output_tokens": None},
            trace={"attempt": 1},
            resolved_model=None,
        ))

    def test_unknown_status_is_rejected(self):
        with self.assertRaises(ContractError):
            validate_result(make_result(status="probably_fine"))

    def test_routes_are_per_item_and_use_the_fixed_vocabulary(self):
        validate_result(make_result(routes=[
            {"item_id": "海外绘本/小老鼠迈尔斯/worldview#003",
             "route": "exclude_soft", "label": "clearly_irrelevant"},
            {"item_id": "海外绘本/小老鼠迈尔斯/worldview#004", "route": "include"},
        ]))

    def test_an_unknown_route_value_is_rejected(self):
        with self.assertRaises(ContractError):
            validate_result(make_result(routes=[
                {"item_id": "k#001", "route": "hope_for_the_best"},
            ]))

    def test_a_route_without_an_item_id_is_rejected(self):
        with self.assertRaises(ContractError):
            validate_result(make_result(routes=[{"route": "include"}]))

    def test_a_succeeded_result_can_carry_routes_with_answers(self):
        validate_result(make_result(routes=[
            {"item_id": "k#001", "route": "escalate_llm", "label": "conflict"},
        ]))

    def test_missing_answer_id_is_rejected(self):
        with self.assertRaises(ContractError):
            validate_answer_ids(make_request(questions={"relevant": {}, "usable": {}}),
                                make_result())

    def test_unknown_answer_id_is_rejected(self):
        with self.assertRaises(ContractError):
            validate_answer_ids(make_request(), make_result(
                answers={"relevant": {"type": "noul", "noul": 0.5}, "surprise": {"type": "noul", "noul": 0.5}},
            ))


def make_policy(**overrides):
    policy = {
        "schema_version": POLICY_SCHEMA,
        "pinned_model": "jev-1.13.0",
        "output_tokens_free": True,
        "price_snapshot": {
            "price_usd_per_million_input_tokens": "0.042",
            "snapshot_date": "2026-09-23",
        },
        "operations": {
            "knowledge_relevance": {
                "policy_version": "knowledge-relevance-v1",
                "calibration_status": "experimental",
                "fallback_route": "escalate_llm",
                "routing": {},
            },
            "text_quality_prefilter": {
                "policy_version": "text-quality-prefilter-v1",
                "calibration_status": "experimental",
                "fallback_route": "escalate_llm",
                "routing": {},
            },
        },
    }
    policy.update(overrides)
    return policy


class PolicyContractTests(unittest.TestCase):
    def test_valid_policy_is_accepted(self):
        validate_policy(make_policy())

    def test_moving_model_alias_is_rejected(self):
        with self.assertRaises(ContractError):
            validate_policy(make_policy(pinned_model="jev-latest"))

    def test_unknown_calibration_status_is_rejected(self):
        policy = make_policy()
        policy["operations"]["knowledge_relevance"]["calibration_status"] = "probably_fine"
        with self.assertRaises(ContractError):
            validate_policy(policy)

    def test_every_supported_operation_needs_a_policy_entry(self):
        policy = make_policy()
        del policy["operations"]["text_quality_prefilter"]
        with self.assertRaises(ContractError):
            validate_policy(policy)

    def test_price_snapshot_must_be_decimal_string_and_date(self):
        with self.assertRaises(ContractError):
            validate_policy(make_policy(price_snapshot={
                "price_usd_per_million_input_tokens": 0.042,
                "snapshot_date": "2026-09-23",
            }))
        with self.assertRaises(ContractError):
            validate_policy(make_policy(price_snapshot={
                "price_usd_per_million_input_tokens": "0.042",
                "snapshot_date": "",
            }))

    def test_unknown_route_value_is_rejected(self):
        policy = make_policy()
        policy["operations"]["knowledge_relevance"]["fallback_route"] = "hope_for_the_best"
        with self.assertRaises(ContractError):
            validate_policy(policy)

    def test_routing_rules_are_refused_until_the_operation_defines_them(self):
        # Phase 1 has no operation questions, so it cannot express a routing
        # rule yet. Shipping an empty table is required, not optional.
        policy = make_policy()
        policy["operations"]["knowledge_relevance"]["routing"] = {
            "relevant": {"clear_route": "exclude_soft"}
        }
        with self.assertRaises(ContractError):
            validate_policy(policy)

    def test_shipped_policy_file_is_valid_and_pins_a_version(self):
        policy = load_policy(default_policy_path())
        self.assertEqual(policy["pinned_model"], "jev-1.13.0")
        self.assertEqual(policy["operations"]["knowledge_relevance"]["calibration_status"],
                         "experimental")

    def test_operation_policy_reads_the_matching_branch(self):
        policy = load_policy(default_policy_path())
        entry = operation_policy(policy, "text_quality_prefilter")
        self.assertEqual(entry["policy_version"], "text-quality-prefilter-v1")

    def test_operation_policy_rejects_an_unknown_operation(self):
        with self.assertRaises(ContractError):
            operation_policy(make_policy(), "write_the_story")


if __name__ == "__main__":
    unittest.main()
