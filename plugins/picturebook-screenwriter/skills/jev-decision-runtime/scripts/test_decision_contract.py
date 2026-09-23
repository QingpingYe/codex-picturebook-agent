import unittest

from decision_contract import (
    ATTEMPT_STATUSES,
    BAND_VALUES,
    CONTEXT_SCHEMA,
    ContractError,
    OPERATIONS,
    POLICY_SCHEMA,
    REQUEST_SCHEMA,
    RESULT_SCHEMA,
    assert_no_credential_fields,
    build_decision_context,
    default_policy_path,
    load_policy,
    operation_cursor,
    operation_policy,
    validate_answer_ids,
    validate_decision_context,
    validate_pending_call,
    validate_policy,
    validate_request,
    validate_result,
    validate_resume_cursor,
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

    def test_a_run_id_that_escapes_the_run_directory_is_rejected(self):
        # run_id is interpolated into the on-disk operation directory, so it
        # must not be able to leave <run_dir>/jev/.
        for bad in ("../../escaped-run", "a/b", "a\\b", "..", "run:1", "run id"):
            with self.subTest(run_id=bad), self.assertRaises(ContractError):
                validate_request(make_request(run_id=bad))

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
                "fallback_route": "include",
                "fallback_label": "uncertain",
                "question_templates": {
                    "relevant": {"type": "noul", "instructions": "该块是否直接影响当前产物？"},
                },
                "routing": {
                    "bands": {"clear_at_or_below": "0.25", "risk_at_or_above": "0.70"},
                    "rules": [
                        {"route": "include", "label": "relevant",
                         "any_of": [{"question_id": "relevant", "bands": ["risk"]}]},
                    ],
                },
            },
            "text_quality_prefilter": {
                "policy_version": "text-quality-prefilter-v1",
                "calibration_status": "experimental",
                "fallback_route": "escalate_llm",
                "fallback_label": "uncertain",
                "question_templates": {
                    "direct_moralizing": {"type": "noul", "instructions": "是否存在直接训诫式说教？"},
                },
                "routing": {
                    "bands": {"clear_at_or_below": "0.25", "risk_at_or_above": "0.70"},
                    "rules": [
                        {"route": "escalate_llm", "label": "risk",
                         "any_of": [{"question_id": "direct_moralizing", "bands": ["risk"]}]},
                    ],
                },
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

    def test_a_routing_table_without_bands_is_refused(self):
        # The phase 1 empty-table guard is replaced by the real schema, so a
        # table that cannot band anything is now the failure mode.
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

    def test_question_templates_are_required_once_routing_exists(self):
        policy = make_policy()
        del policy["operations"]["knowledge_relevance"]["question_templates"]
        with self.assertRaises(ContractError):
            validate_policy(policy)

    def test_a_rule_may_only_reference_declared_question_templates(self):
        policy = make_policy()
        policy["operations"]["knowledge_relevance"]["routing"] = {
            "bands": {"clear_at_or_below": "0.25", "risk_at_or_above": "0.70"},
            "rules": [{"route": "escalate_llm",
                       "any_of": [{"question_id": "not_declared", "bands": ["risk"]}]}],
        }
        with self.assertRaises(ContractError):
            validate_policy(policy)

    def test_a_rule_needs_exactly_one_of_any_of_or_all_of(self):
        policy = make_policy()
        policy["operations"]["knowledge_relevance"]["routing"] = {
            "bands": {"clear_at_or_below": "0.25", "risk_at_or_above": "0.70"},
            "rules": [{"route": "include",
                       "any_of": [{"question_id": "relevant", "bands": ["risk"]}],
                       "all_of": [{"question_id": "relevant", "bands": ["risk"]}]}],
        }
        with self.assertRaises(ContractError):
            validate_policy(policy)

    def test_clear_band_must_stay_below_the_risk_band(self):
        policy = make_policy()
        policy["operations"]["knowledge_relevance"]["routing"] = {
            "bands": {"clear_at_or_below": "0.80", "risk_at_or_above": "0.70"},
            "rules": [{"route": "include",
                       "any_of": [{"question_id": "relevant", "bands": ["risk"]}]}],
        }
        with self.assertRaises(ContractError):
            validate_policy(policy)

    def test_an_unknown_band_name_is_rejected(self):
        policy = make_policy()
        policy["operations"]["knowledge_relevance"]["routing"] = {
            "bands": {"clear_at_or_below": "0.25", "risk_at_or_above": "0.70"},
            "rules": [{"route": "include",
                       "any_of": [{"question_id": "relevant", "bands": ["maybe"]}]}],
        }
        with self.assertRaises(ContractError):
            validate_policy(policy)

    def test_band_vocabulary_is_published(self):
        self.assertEqual(BAND_VALUES, ("clear", "grey", "risk"))

    def test_max_items_per_request_is_optional_and_positive_when_present(self):
        policy = make_policy()
        policy["operations"]["knowledge_relevance"]["max_items_per_request"] = 10
        validate_policy(policy)
        policy["operations"]["knowledge_relevance"]["max_items_per_request"] = 0
        with self.assertRaises(ContractError):
            validate_policy(policy)


def make_context(**overrides):
    context = build_decision_context(
        mode="jev_assisted",
        external_text_processing_acknowledged=True,
        selected_at="2026-09-23T10:30:00+08:00",
    )
    context.update(overrides)
    return context


def make_pending_call(**overrides):
    pending = {
        "operation_id": "20260923-example-0001-knowledge_relevance",
        "operation": "knowledge_relevance",
        "request_fingerprint": "sha256:" + "1" * 64,
        "policy_version": "knowledge-relevance-v1",
        "requested_model": "jev-1.13.0",
        "attempt_status": "pending",
        "input_refs": [
            {
                "ref_id": "海外绘本/小老鼠迈尔斯/worldview",
                "kind": "knowledge_page",
                "revisions": {"node-a": "17"},
            }
        ],
    }
    pending.update(overrides)
    return pending


class DecisionContextTests(unittest.TestCase):
    def test_context_defaults_to_the_initial_cursor(self):
        context = make_context()
        self.assertEqual(context["schema_version"], CONTEXT_SCHEMA)
        self.assertEqual(context["resume_cursor"], "after_execution_choice")
        self.assertEqual(context["credential_status"], "unchecked")
        self.assertIsNone(context["pending_call"])
        validate_decision_context(context)

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ContractError):
            validate_decision_context(make_context(mode="jev_probably"))

    def test_unconfirmed_selection_is_rejected(self):
        with self.assertRaises(ContractError):
            validate_decision_context(make_context(selection_status="maybe"))

    def test_unknown_credential_status_is_rejected(self):
        with self.assertRaises(ContractError):
            validate_decision_context(make_context(credential_status="checking"))

    def test_context_accepts_a_pending_call(self):
        validate_decision_context(make_context(
            credential_status="waiting_for_jev_key",
            pending_call=make_pending_call(attempt_status="failed"),
        ))

    def test_context_carries_no_credential_shaped_field(self):
        with self.assertRaises(ContractError):
            validate_decision_context(make_context(api_key="sk-nope"))

    def test_resume_cursor_vocabulary(self):
        validate_resume_cursor("after_execution_choice")
        validate_resume_cursor(operation_cursor("20260923-example-0001-knowledge_relevance"))
        for bad in ("", "later", "after_operation:"):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                validate_resume_cursor(bad)

    def test_pending_call_attempt_status_vocabulary(self):
        for status in ATTEMPT_STATUSES:
            with self.subTest(status=status):
                validate_pending_call(make_pending_call(attempt_status=status))
        with self.assertRaises(ContractError):
            validate_pending_call(make_pending_call(attempt_status="probably"))

    def test_pending_call_needs_at_least_one_input_ref(self):
        with self.assertRaises(ContractError):
            validate_pending_call(make_pending_call(input_refs=[]))

    def test_pending_call_rejects_unknown_operation(self):
        with self.assertRaises(ContractError):
            validate_pending_call(make_pending_call(operation="write_the_story"))


if __name__ == "__main__":
    unittest.main()
