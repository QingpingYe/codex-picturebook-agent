import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import screening_runner
from decision_contract import default_policy_path, load_policy
from jev_client import (
    API_KEY_ENV,
    FakeTransport,
    JevClient,
    JevTransportOutcomeUnknown,
    TransportResponse,
)
from jev_runner import RunnerConfig, lease_path, operation_id_from_request
from page_quality import parse_script_pages
from quality_gate import PROXY_SOURCE, Finding
from redline_catalog import RedlineRule
from screening_runner import (
    OPERATION,
    build_request,
    detect_proxy_conflicts,
    page_windows,
    plan_batches,
    redline_dimensions,
    run_screening,
    screening_items,
)

SCRIPT = """# 学会分享

| # | 页码 | Text | 插图 |
| --- | --- | --- | --- |
| 1 | 1 | Miles found a red ball. / He held it tight. | 男孩抱着红球 |
| 2 | 2 | "Mine!" he said. | 男孩转身 |
| 3 | 3 | Miles rolled the ball to her. / "Let's play!" | 两人一起玩 |
"""

PAGES = parse_script_pages(SCRIPT)

RULES = (
    RedlineRule("redline-aaa", "变勇敢了", "禁止直接写成变勇敢了",
                "海外绘本/小老鼠迈尔斯/corrections", 17),
    RedlineRule("redline-bbb", "魔法解决一切", "禁止用魔法解决冲突",
                "海外绘本/小老鼠迈尔斯/corrections", 17),
)

DIMENSIONS = ("direct_moralizing", "age_comprehension_risk", "read_aloud_friction",
              "weak_page_turn_motivation", "emotion_told_not_shown")


def bundle():
    return {
        "items": ({"key": "海外绘本/小老鼠迈尔斯/corrections", "doc_token": "doxcnExample",
                   "revision_id": 17, "title": "海外绘本/小老鼠迈尔斯/corrections",
                   "content": "# 纠正台账\n\n## 强制性禁止条目\n\n禁止写成\"变勇敢了\"。\n",
                   "source_revisions": {"node-a": "17"}, "status": "published",
                   "index_synced": True},),
        "warnings": (), "offline": False, "fetched_at": "2026-09-23T10:30:00+08:00",
    }


def noul(value):
    return {"type": "noul", "noul": value}


class PageWindowTests(unittest.TestCase):
    def test_a_single_page_window_per_page_by_default(self):
        windows = page_windows(PAGES)
        self.assertEqual([window["item_id"] for window in windows],
                         ["page-1", "page-2", "page-3"])

    def test_a_wider_window_covers_neighbours(self):
        windows = page_windows(PAGES, width=2)
        self.assertIn("page-2", windows[0]["item_id"])
        self.assertIn("page-3", windows[0]["item_id"])

    def test_a_window_carries_the_concatenated_text(self):
        windows = page_windows(PAGES, width=2)
        self.assertIn("Mine!", windows[0]["text"])
        self.assertIn("Let's play!", windows[0]["text"])

    def test_a_window_wider_than_the_script_lists_every_page_once(self):
        # A width configured wider than the book must still produce one window
        # per page, none of them empty, and none of them skipping a page.
        windows = page_windows(PAGES, width=10)
        self.assertEqual(len(windows), len(PAGES))
        self.assertEqual([window["page_no"] for window in windows], ["1", "2", "3"])
        for window in windows:
            self.assertTrue(window["text"].strip())


class RedlineDimensionTests(unittest.TestCase):
    def test_every_active_red_line_becomes_a_dimension(self):
        self.assertEqual(redline_dimensions(RULES),
                         ("redline:redline-aaa", "redline:redline-bbb"))

    def test_an_empty_catalog_produces_no_dimensions(self):
        self.assertEqual(redline_dimensions(()), ())

    def test_dimensions_keep_catalog_order(self):
        self.assertEqual(redline_dimensions(RULES)[0], "redline:redline-aaa")


class ScreeningItemTests(unittest.TestCase):
    def test_one_item_per_page(self):
        items = screening_items(PAGES, RULES)
        self.assertEqual([item["item_id"] for item in items],
                         ["page-1", "page-2", "page-3"])

    def test_an_item_carries_both_quality_and_redline_dimensions(self):
        item = screening_items(PAGES, RULES)[0]
        for dimension in DIMENSIONS:
            with self.subTest(dimension=dimension):
                if dimension == "weak_page_turn_motivation":
                    self.assertIn(dimension, item["dimensions"])
        self.assertIn("redline:redline-aaa", item["dimensions"])
        self.assertIn("redline:redline-bbb", item["dimensions"])

    def test_the_last_page_item_omits_the_page_turn_dimension(self):
        item = screening_items(PAGES, RULES)[-1]
        self.assertNotIn("weak_page_turn_motivation", item["dimensions"])
        self.assertIn("direct_moralizing", item["dimensions"])

    def test_an_item_carries_the_page_text_and_facts(self):
        item = screening_items(PAGES, RULES)[1]
        self.assertIn("Mine!", item["text"])
        self.assertIn("char_count", item["facts"])

    def test_a_wider_window_covers_neighbours(self):
        items = screening_items(PAGES, RULES, window_width=2)
        self.assertIn("Mine!", items[0]["text"])
        self.assertIn("Let's play!", items[0]["text"])

    def test_no_rules_still_produces_page_items(self):
        items = screening_items(PAGES, ())
        self.assertEqual(len(items), len(PAGES))
        self.assertIn("direct_moralizing", items[0]["dimensions"])

    def test_no_pages_produces_no_items(self):
        self.assertEqual(screening_items((), RULES), ())

    def test_a_wordless_page_produces_no_items(self):
        # Wordless spreads are normal in picture books and spec §11.1 lists
        # them as a sample category. Asking Jev whether an empty page "tells
        # instead of shows" is a made-up question, and whatever it answered
        # must not be counted as a cleared item either.
        pages = (
            {"no": "1", "text": "Miles found a red ball."},
            {"no": "2", "text": ""},
            {"no": "3", "text": "   "},
        )
        items = screening_items(pages, RULES)
        self.assertEqual([item["item_id"] for item in items], ["page-1"])


class BatchTests(unittest.TestCase):
    def test_batches_respect_the_item_cap(self):
        items = [{"item_id": f"i{index}"} for index in range(7)]
        self.assertEqual([len(batch) for batch in plan_batches(items, 3)], [3, 3, 1])

    def test_a_non_positive_cap_is_refused(self):
        with self.assertRaises(ValueError):
            plan_batches([{"item_id": "i"}], 0)


PAGE_BATCH = ({"item_id": "page-1", "page_numbers": ["1"], "text": "Miles ran.",
               "facts": {"char_count": 10, "sentence_count": 1, "max_line_repeat": 1},
               "dimensions": DIMENSIONS},)

# An item carries the literal of every red line it is asked about, which is what
# lets the question name the rule instead of only its id. `screening_items`
# supplies this map from the catalog.
REDLINE_BATCH = ({"item_id": "page-1", "page_numbers": ["1"], "text": "Miles ran.",
                  "facts": {"char_count": 10, "sentence_count": 1, "max_line_repeat": 1},
                  "dimensions": ("redline:redline-aaa",),
                  "redline_patterns": {"redline:redline-aaa": "变勇敢了"}},)


class BuildRequestTests(unittest.TestCase):
    def _request(self, batch):
        return build_request(
            run_id="20260923-example-0001", policy=load_policy(default_policy_path()),
            age_band="3-6", batch=batch, batch_index=1,
            benchmark_case_id="sha256:" + "0" * 64, bundle=bundle(),
        )

    def test_questions_are_keyed_by_item_and_dimension(self):
        request = self._request(PAGE_BATCH)
        self.assertIn("page-1::direct_moralizing", request["questions"])
        self.assertIn("page-1::emotion_told_not_shown", request["questions"])

    def test_the_placeholder_is_replaced_by_the_item_id(self):
        instructions = self._request(PAGE_BATCH)[
            "questions"]["page-1::direct_moralizing"]["instructions"]
        self.assertIn("page-1", instructions)
        self.assertNotIn("<page>", instructions)

    def test_a_redline_dimension_gets_a_question_naming_the_pattern(self):
        request = self._request(REDLINE_BATCH)
        instructions = request["questions"]["page-1::redline:redline-aaa"]["instructions"]
        self.assertIn("变勇敢了", instructions)

    def test_the_state_holds_the_page_text_and_its_facts_together(self):
        page = self._request(PAGE_BATCH)["state"]["pages"]["page-1"]
        self.assertEqual(page["text"], "Miles ran.")
        self.assertEqual(page["char_count"], 10)

    def test_a_page_appears_in_the_state_exactly_once(self):
        request = self._request(PAGE_BATCH)
        self.assertEqual(list(request["state"]["pages"]), ["page-1"])

    def test_the_state_names_the_age_band(self):
        self.assertEqual(self._request(PAGE_BATCH)["state"]["task"]["age_band"], "3-6")

    def test_the_instance_keeps_batches_apart(self):
        self.assertEqual(self._request(PAGE_BATCH)["operation_instance"], "batch-001")


def all_clear_answers(items):
    """Every asked question for every item, answered with a clear-band value."""

    answers = {}
    for item in items:
        for dimension in item["dimensions"]:
            answers[f"{item['item_id']}::{dimension}"] = noul(0.05)
    return answers


class RunScreeningTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)
        self.policy = load_policy(default_policy_path())
        self.config = RunnerConfig(run_dir=self.run_dir, policy=self.policy)

    def tearDown(self):
        self._tmp.cleanup()

    def _client(self, answers, status_code=200):
        body = json.dumps({"model": "jev-1.13.0", "answers": answers,
                           "usage": {"input_tokens": 300, "output_tokens": 30}})
        return JevClient(FakeTransport(responses=[TransportResponse(status_code, body, {})]),
                         environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None)

    def _run(self, client, rules=RULES, proxy_findings=()):
        return run_screening(
            run_id="20260923-example-0001", policy=self.policy, pages=PAGES,
            rules=rules, bundle=bundle(), config=self.config, client=client,
            age_band="3-6", proxy_findings=proxy_findings,
        )

    def _clear_client(self, rules=RULES):
        return self._client(all_clear_answers(screening_items(PAGES, rules)))

    def test_every_page_dimension_and_every_red_line_is_screened(self):
        outcome = self._run(self._clear_client())
        dimensions = {decision.dimension for decision in outcome.decisions}
        for dimension in DIMENSIONS:
            with self.subTest(dimension=dimension):
                self.assertIn(dimension, dimensions)
        self.assertIn("redline:redline-aaa", dimensions)
        self.assertIn("redline:redline-bbb", dimensions)
        self.assertEqual(outcome.catalog_size, len(RULES))

    def test_a_clear_page_dimension_is_screened_clear(self):
        outcome = self._run(self._clear_client())
        self.assertTrue([d for d in outcome.decisions if d.outcome == "screened_clear"])

    def test_the_experimental_policy_keeps_the_llm_review_running(self):
        from screening import cleared_items, may_skip_llm_review

        outcome = self._run(self._clear_client())
        calibration = self.policy["operations"][OPERATION]["calibration_status"]
        self.assertEqual(calibration, "experimental")
        for decision in cleared_items(outcome.decisions):
            self.assertFalse(may_skip_llm_review(decision, calibration))

    def test_a_risky_dimension_lands_in_the_escalation_package(self):
        answers = all_clear_answers(screening_items(PAGES, RULES))
        answers["page-2::direct_moralizing"] = noul(0.95)
        outcome = self._run(self._client(answers))
        self.assertIn("page-2::direct_moralizing",
                      {entry["item_id"] for entry in outcome.escalation_package})

    def test_a_grey_dimension_lands_in_the_escalation_package(self):
        answers = all_clear_answers(screening_items(PAGES, RULES))
        answers["page-2::read_aloud_friction"] = noul(0.5)
        outcome = self._run(self._client(answers))
        self.assertIn("page-2::read_aloud_friction",
                      {entry["item_id"] for entry in outcome.escalation_package})

    def test_the_escalation_package_carries_the_page_text(self):
        answers = all_clear_answers(screening_items(PAGES, RULES))
        answers["page-2::direct_moralizing"] = noul(0.95)
        outcome = self._run(self._client(answers))
        entry = next(e for e in outcome.escalation_package
                     if e["item_id"] == "page-2::direct_moralizing")
        self.assertIn("Mine!", entry["evidence"])

    def test_the_last_page_is_never_asked_about_page_turn_motivation(self):
        outcome = self._run(self._clear_client())
        asked = {decision.item_id for decision in outcome.decisions}
        self.assertNotIn("page-3::weak_page_turn_motivation", asked)
        self.assertIn("page-2::weak_page_turn_motivation", asked)

    def test_a_failed_request_produces_runtime_failures_not_clears(self):
        body = json.dumps({"model": "jev-1.13.0", "answers": {}, "usage": {}})
        client = JevClient(FakeTransport(responses=[TransportResponse(200, body, {})]),
                           environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None)
        outcome = self._run(client)
        self.assertEqual([d for d in outcome.decisions if d.outcome == "screened_clear"], [])
        self.assertEqual(outcome.results[0]["status"], "failed")
        self.assertTrue(all(d.outcome == "runtime_failure" for d in outcome.decisions))

    def test_an_empty_catalog_is_reported_as_a_gap(self):
        outcome = self._run(self._clear_client(rules=()), rules=())
        self.assertEqual(outcome.catalog_size, 0)
        self.assertTrue(outcome.summary["catalog_gap"])

    def test_no_rules_still_screens_the_page_dimensions(self):
        outcome = self._run(self._clear_client(rules=()), rules=())
        self.assertTrue([d for d in outcome.decisions
                         if d.dimension == "direct_moralizing"])

    def test_the_summary_reports_the_screening_ratios(self):
        outcome = self._run(self._clear_client())
        self.assertEqual(outcome.summary["total"], len(outcome.decisions))
        self.assertIn("escalation_ratio", outcome.summary)
        self.assertIn("screened_clear_ratio", outcome.summary)


class ProxyConflictTests(unittest.TestCase):
    def test_a_proxy_hit_that_was_not_escalated_is_a_conflict(self):
        from screening import ScreeningDecision

        decisions = (ScreeningDecision("page-2", "direct_moralizing",
                                      "screened_clear", {"direct_moralizing": 0.05}),
                     ScreeningDecision("page-1", "direct_moralizing",
                                      "escalate_llm", {"direct_moralizing": 0.9}))
        findings = (Finding("redline-aaa", PROXY_SOURCE, "FAIL", "候选命中", "变勇敢了"),)
        conflicts = detect_proxy_conflicts(findings, decisions)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["reason"], "proxy_hit_not_escalated")

    def test_no_proxy_findings_means_no_conflicts(self):
        self.assertEqual(detect_proxy_conflicts((), ()), ())

    def test_a_red_line_dimension_that_escalated_is_not_a_conflict(self):
        # The two signals agree exactly when the rule's own question escalated:
        # a cleared page dimension is not a verdict on that rule.
        from screening import ScreeningDecision

        decisions = (ScreeningDecision("page-2", "redline:redline-aaa",
                                      "escalate_llm", {"redline:redline-aaa": 0.9}),
                     ScreeningDecision("page-1", "direct_moralizing",
                                      "screened_clear", {"direct_moralizing": 0.05}))
        findings = (Finding("redline-aaa", PROXY_SOURCE, "FAIL", "候选命中", "变勇敢了"),)
        self.assertEqual(detect_proxy_conflicts(findings, decisions), ())


class AnswerReadingGuardTests(unittest.TestCase):
    """A paid-for answer the router cannot read still settles as a decision."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)
        self.policy = load_policy(default_policy_path())
        self.config = RunnerConfig(run_dir=self.run_dir, policy=self.policy)

    def tearDown(self):
        self._tmp.cleanup()

    def _client(self):
        body = json.dumps({
            "model": "jev-1.13.0",
            "answers": all_clear_answers(screening_items(PAGES, RULES)),
            "usage": {"input_tokens": 300, "output_tokens": 30},
        })
        return JevClient(FakeTransport(responses=[TransportResponse(200, body, {})]),
                         environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None)

    def test_an_unreadable_answer_never_escapes_the_screening_unit(self):
        # The guard covers the whole UNREADABLE_ANSWER_ERRORS family rather than
        # the RoutingError `screening_decision` already catches, so a KeyError,
        # a TypeError or a bad Decimal cannot abort a call that was paid for.
        with mock.patch.object(screening_runner, "route_item",
                               side_effect=KeyError("noul")):
            outcome = run_screening(
                run_id="20260923-example-0001", policy=self.policy, pages=PAGES,
                rules=RULES, bundle=bundle(), config=self.config, client=self._client(),
                age_band="3-6",
            )
        self.assertTrue(outcome.decisions)
        self.assertTrue(all(d.outcome == "runtime_failure" for d in outcome.decisions))
        self.assertTrue(all(d.reason.startswith("routing_error") for d in outcome.decisions))
        self.assertEqual(outcome.routes, ())


class _NeverCalledClient:
    """A client that fails the test if a dispatch reaches the transport."""

    def __init__(self):
        self.calls = 0

    def call(self, request, **kwargs):
        self.calls += 1
        raise AssertionError("a batch was sent while another runner held its lease")


class _CountingClient:
    """A real client that also records how many dispatches reached it."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0

    def call(self, request, **kwargs):
        self.calls += 1
        return self._inner.call(request, **kwargs)


class StoredBatchReuseTests(unittest.TestCase):
    """A batch is screened once: the run directory decides reuse and re-sends.

    Re-running a screen used to send every batch again, so the same question was
    paid for twice and a possibly-billed attempt was overwritten along with the
    only record of it. These tests pin the sibling path's behaviour: a settled
    verdict for this exact request is reused, and every record that cannot be
    read as one keeps its batch out of the screen and names itself.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)
        self.policy = load_policy(default_policy_path())
        self.config = RunnerConfig(run_dir=self.run_dir, policy=self.policy)

    def tearDown(self):
        self._tmp.cleanup()

    def _client(self, answers, status_code=200):
        body = json.dumps({"model": "jev-1.13.0", "answers": answers,
                           "usage": {"input_tokens": 300, "output_tokens": 30}})
        return JevClient(FakeTransport(responses=[TransportResponse(status_code, body, {})]),
                         environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None)

    def _clear_client(self, rules=RULES):
        return self._client(all_clear_answers(screening_items(PAGES, rules)))

    def _run(self, client, rules=RULES):
        return run_screening(
            run_id="20260923-example-0001", policy=self.policy, pages=PAGES,
            rules=rules, bundle=bundle(), config=self.config, client=client,
            age_band="3-6",
        )

    def _operation_dir(self):
        """The single operation directory this three-page book produces."""

        (request_file,) = sorted(self.run_dir.glob("jev/*/request.json"))
        return request_file.parent

    def test_a_settled_batch_is_reused_instead_of_paid_for_again(self):
        first = self._run(self._clear_client())
        self.assertTrue([d for d in first.decisions if d.outcome == "screened_clear"])

        # The second run must not reach the provider at all: the verdict for
        # this exact request is already on disk.
        second = self._run(_NeverCalledClient())
        self.assertEqual(second.decisions, first.decisions)
        self.assertEqual(second.results, first.results)
        self.assertEqual(second.escalation_package, first.escalation_package)
        self.assertEqual(second.summary["screened_clear"], first.summary["screened_clear"])
        self.assertEqual(second.blocked_records, ())

    def test_a_stored_verdict_for_other_inputs_does_not_cover_the_new_pages(self):
        self._run(self._clear_client())

        # A different catalog is a different request, so the stored verdict is
        # not a verdict on this one and the batch has to be screened again.
        client = _CountingClient(self._clear_client(rules=()))
        outcome = self._run(client, rules=())
        self.assertEqual(client.calls, 1)
        self.assertEqual(outcome.catalog_size, 0)
        self.assertTrue(outcome.summary["catalog_gap"])
        self.assertTrue([d for d in outcome.decisions
                         if d.dimension == "direct_moralizing"])
        self.assertEqual(outcome.blocked_records, ())

    def test_an_open_attempt_is_never_re_sent_and_names_the_record(self):
        ambiguous = JevClient(
            FakeTransport(error=JevTransportOutcomeUnknown("connection lost")),
            environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None,
        )
        first = self._run(ambiguous)
        self.assertEqual([result["status"] for result in first.results],
                         ["outcome_unknown"])

        client = _NeverCalledClient()
        second = self._run(client)
        self.assertEqual(client.calls, 0)
        self.assertEqual(second.results, ())
        self.assertTrue(second.decisions)
        self.assertTrue(all(d.outcome == "runtime_failure" for d in second.decisions))
        self.assertTrue(all(d.reason == "open_attempt" for d in second.decisions))
        (entry,) = second.blocked_records
        self.assertEqual(entry["reason"], "open_attempt")
        self.assertTrue(entry["path"].endswith("pending.json"))
        self.assertEqual(entry["operation_id"], self._operation_dir().name)

    def test_a_blocked_batch_stops_the_batches_behind_it(self):
        # One page per batch, so pages 2 and 3 are separate operations a second
        # run would have to open behind the blocked one.
        self.policy["operations"][OPERATION]["max_items_per_request"] = 1
        ambiguous = JevClient(
            FakeTransport(error=JevTransportOutcomeUnknown("connection lost")),
            environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None,
        )
        self._run(ambiguous)

        client = _NeverCalledClient()
        outcome = self._run(client)
        self.assertEqual(client.calls, 0)
        self.assertTrue(all(d.reason == "open_attempt" for d in outcome.decisions))
        # Every page is accounted for: the blocked batch and the batches the
        # screen never reached all carry the same reason, and none is cleared.
        self.assertEqual(
            {d.item_id.split("::")[0] for d in outcome.decisions},
            {"page-1", "page-2", "page-3"},
        )
        self.assertEqual([entry["reason"] for entry in outcome.blocked_records],
                         ["open_attempt"])

    def test_an_unreadable_stored_result_blocks_the_batch(self):
        self._run(self._clear_client())
        (result_file,) = sorted(self.run_dir.glob("jev/*/result.json"))
        result_file.write_text("{ this is not json", encoding="utf-8")

        client = _NeverCalledClient()
        outcome = self._run(client)
        self.assertEqual(client.calls, 0)
        self.assertEqual(outcome.results, ())
        self.assertTrue(all(d.reason == "unreadable_result" for d in outcome.decisions))
        (entry,) = outcome.blocked_records
        self.assertEqual(entry["reason"], "unreadable_result")
        self.assertEqual(entry["path"], str(result_file))

    def test_an_unreadable_stored_request_blocks_the_batch(self):
        self._run(self._clear_client())
        (request_file,) = sorted(self.run_dir.glob("jev/*/request.json"))
        request_file.write_text("not json either", encoding="utf-8")

        client = _NeverCalledClient()
        outcome = self._run(client)
        self.assertEqual(client.calls, 0)
        self.assertEqual(outcome.results, ())
        self.assertTrue(all(d.reason == "unreadable_request" for d in outcome.decisions))
        (entry,) = outcome.blocked_records
        self.assertEqual(entry["reason"], "unreadable_request")
        self.assertEqual(entry["path"], str(request_file))

    def test_a_failed_batch_names_its_error_class_and_a_later_run_retries_it(self):
        body = json.dumps({"model": "jev-1.13.0", "answers": {}, "usage": {}})
        failing = JevClient(FakeTransport(responses=[TransportResponse(200, body, {})]),
                            environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None)
        first = self._run(failing)
        self.assertEqual(first.results[0]["status"], "failed")
        self.assertTrue(all(d.outcome == "runtime_failure" for d in first.decisions))
        self.assertTrue(all(d.reason == "failed:incomplete_response"
                            for d in first.decisions))
        self.assertTrue(first.escalation_package)
        self.assertTrue(all(entry["reason"] == "failed:incomplete_response"
                            for entry in first.escalation_package))

        # A settled failure leaves no billed attempt behind it, so re-running
        # the screen retries the batch instead of blocking it forever.
        client = _CountingClient(self._clear_client())
        second = self._run(client)
        self.assertEqual(client.calls, 1)
        self.assertTrue([d for d in second.decisions if d.outcome == "screened_clear"])
        self.assertEqual(second.blocked_records, ())


class DispatchDisciplineTests(unittest.TestCase):
    """The screen dispatches through the shared runner's leased entry point."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)
        self.policy = load_policy(default_policy_path())
        self.config = RunnerConfig(run_dir=self.run_dir, policy=self.policy)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_batch_another_runner_holds_is_neither_sent_nor_cleared(self):
        # The book fits one batch, so a held lease covers every page of it.
        request = build_request(
            run_id="20260923-example-0001", policy=self.policy, age_band="3-6",
            batch=screening_items(PAGES, RULES)[:1], batch_index=1,
            benchmark_case_id="sha256:" + "0" * 64, bundle=bundle(),
        )
        lease = lease_path(self.run_dir, operation_id_from_request(request))
        lease.parent.mkdir(parents=True, exist_ok=True)
        lease.write_text(json.dumps({
            "operation_id": lease.parent.name,
            "holder": "another-runner",
            "started_at": "2026-09-23T10:00:00+00:00",
            "expires_at": "2999-01-01T00:00:00+00:00",
        }), encoding="utf-8")

        client = _NeverCalledClient()
        outcome = run_screening(
            run_id="20260923-example-0001", policy=self.policy, pages=PAGES,
            rules=RULES, bundle=bundle(), config=self.config, client=client,
            age_band="3-6",
        )
        self.assertEqual(client.calls, 0)
        self.assertTrue(outcome.decisions)
        self.assertTrue(all(d.outcome == "runtime_failure" for d in outcome.decisions))
        self.assertTrue(all(d.reason == "lease_held" for d in outcome.decisions))
        self.assertEqual(outcome.results, ())


if __name__ == "__main__":
    unittest.main()
