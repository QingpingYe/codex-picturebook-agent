import json
import sys
import tempfile
import unittest
from pathlib import Path

# The operation composes the knowledge side with the shared decision runtime,
# so this test imports both skill's script directories.
RUNTIME_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "jev-decision-runtime" / "scripts"
)
if str(RUNTIME_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SCRIPTS))

from decision_contract import load_policy, default_policy_path  # noqa: E402
from chunker import Chunk  # noqa: E402
from jev_client import API_KEY_ENV, FakeTransport, JevClient, TransportResponse  # noqa: E402
from jev_runner import RunnerConfig  # noqa: E402
from relevance import (  # noqa: E402
    MAX_STATE_CHARS,
    OPERATION,
    ContextBudgetError,
    benchmark_input,
    build_request,
    dependency_bundle,
    filtered_bundle,
    plan_batches,
    screen_candidates,
)
from required_marking import mark_bundle  # noqa: E402

RUN_ID = "20260923-example-0001"
KEY = "海外绘本/小老鼠迈尔斯/worldview"
TWIN_KEY = "海外绘本/迈尔斯续集/worldview"

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


def evidence(key=KEY, body=WORLDVIEW_BODY):
    return {
        "key": key,
        "doc_token": "doxcnExample",
        "revision_id": 17,
        "title": key,
        "content": body,
        "source_revisions": {"node-a": "17"},
        "status": "published",
        "index_synced": True,
    }


def bundle():
    return {
        "items": (evidence(),),
        "warnings": (),
        "offline": False,
        "fetched_at": "2026-09-23T10:30:00+08:00",
    }


def noul(value):
    return {"type": "noul", "noul": value}


def answers_for(chunks, values=(0.05, 0.05, 0.05, 0.05)):
    payload = {}
    for chunk in chunks:
        for question_id, value in zip(
            ("relevant", "usable_evidence", "contradicts_task_assumption",
             "instruction_like_content"),
            values,
        ):
            payload[f"{chunk.chunk_id}::{question_id}"] = noul(value)
    return payload


class BatchTests(unittest.TestCase):
    def test_batches_respect_the_item_cap(self):
        candidates = [chunk for chunk in mark_bundle(bundle()) if not chunk.required]
        batches = plan_batches(candidates, 2)
        self.assertEqual([len(batch) for batch in batches], [2, 1])

    def test_no_candidates_produces_no_batches(self):
        self.assertEqual(plan_batches((), 10), ())

    def test_a_non_positive_cap_is_refused(self):
        with self.assertRaises(ValueError):
            plan_batches(mark_bundle(bundle()), 0)


class BenchmarkInputTests(unittest.TestCase):
    def test_the_same_bundle_and_artifact_type_share_a_case_id(self):
        self.assertEqual(
            benchmark_input(bundle(), "script"),
            benchmark_input(bundle(), "script"),
        )

    def test_changing_a_revision_changes_the_case_id(self):
        moved = bundle()
        moved["items"][0]["revision_id"] = 18
        self.assertNotEqual(
            benchmark_input(bundle(), "script"), benchmark_input(moved, "script")
        )

    def test_changing_the_artifact_type_changes_the_case_id(self):
        self.assertNotEqual(
            benchmark_input(bundle(), "script"), benchmark_input(bundle(), "outline")
        )


class BuildRequestTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy(default_policy_path())
        self.chunks = tuple(
            chunk for chunk in mark_bundle(bundle()) if not chunk.required
        )

    def _build(self, **overrides):
        arguments = {
            "run_id": RUN_ID, "policy": self.policy, "artifact_type": "script",
            "task_description": "起草第 5 页", "brief": "分享主题，3-6 岁",
            "batch": self.chunks, "batch_index": 1,
            "benchmark_case_id": "sha256:" + "0" * 64, "bundle": bundle(),
        }
        arguments.update(overrides)
        return build_request(**arguments)

    def test_questions_are_keyed_by_item_and_question_id(self):
        request = self._build()
        suffix = f"{self.chunks[0].chunk_id}::relevant"
        self.assertIn(suffix, request["questions"])

    def test_the_request_declares_the_operation(self):
        self.assertEqual(self._build()["operation"], OPERATION)

    def test_the_template_placeholder_is_replaced_by_the_item_id(self):
        request = self._build()
        instructions = request["questions"][f"{self.chunks[0].chunk_id}::relevant"]["instructions"]
        self.assertIn(self.chunks[0].chunk_id, instructions)
        self.assertNotIn("<item>", instructions)

    def test_the_state_carries_only_the_batch_and_the_task(self):
        request = self._build()
        self.assertEqual(set(request["state"]), {"task", "chunks"})
        self.assertEqual(set(request["state"]["chunks"]), {c.chunk_id for c in self.chunks})

    def test_context_refs_carry_the_page_version_vector(self):
        request = self._build()
        self.assertEqual(request["context_refs"], [{
            "ref_id": KEY, "kind": "knowledge_page", "revisions": {"node-a": "17"},
        }])

    def test_the_instance_keeps_batches_apart(self):
        request = self._build(
            task_description="x", brief="y", batch_index=2,
        )
        self.assertEqual(request["operation_instance"], "batch-002")


class ContextBudgetTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy(default_policy_path())

    def _build(self, batch):
        return build_request(
            run_id=RUN_ID, policy=self.policy, artifact_type="script",
            task_description="起草第 5 页", brief="分享主题，3-6 岁",
            batch=batch, batch_index=1, benchmark_case_id="sha256:" + "0" * 64,
            bundle=bundle(),
        )

    def test_a_normal_batch_stays_inside_the_budget(self):
        request = self._build(tuple(mark_bundle(bundle())))
        state = json.dumps(request["state"], ensure_ascii=False)
        self.assertLess(len(state), MAX_STATE_CHARS)

    def test_an_over_budget_batch_is_refused_instead_of_dispatched(self):
        # Hard constraints may never be dropped to make room, so a batch that
        # cannot fit has to fail here instead of coming back as a paid 422.
        batch = tuple(
            Chunk(
                chunk_id=f"{KEY}#{index:03d}",
                key=KEY,
                doc_token="doxcnExample",
                revision_id=17,
                heading_path=("创作红线不变量",),
                text="迈尔斯不能飞行。" * 2400,
                required=True,
                required_reason="worldview_red_line",
            )
            for index in range(20)
        )
        with self.assertRaises(ContextBudgetError):
            self._build(batch)

    def test_the_budget_error_is_reachable_from_the_documented_error_handler(self):
        # Every runner CLI in this plugin reports a ValueError as a JSON error,
        # so a reported budget gap has to be that kind of error rather than one
        # that escapes the handler as a traceback.
        self.assertTrue(issubclass(ContextBudgetError, ValueError))


class ScreeningTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)
        self.policy = load_policy(default_policy_path())
        self.config = RunnerConfig(run_dir=self.run_dir, policy=self.policy)

    def tearDown(self):
        self._tmp.cleanup()

    def _screen(self, answers, responses=None, bundle_override=None, policy=None):
        body = json.dumps({"model": "jev-1.13.0", "answers": answers,
                           "usage": {"input_tokens": 120, "output_tokens": 8}})
        transport = FakeTransport(responses=responses or [TransportResponse(200, body, {})])
        client = JevClient(transport, environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None)
        policy = policy or self.policy
        return screen_candidates(
            run_id=RUN_ID, policy=policy, artifact_type="script",
            task_description="起草第 5 页", brief="分享主题",
            bundle=bundle_override or bundle(),
            config=RunnerConfig(run_dir=self.run_dir, policy=policy),
            client=client,
        )

    def test_a_required_chunk_is_kept_even_when_the_model_calls_it_irrelevant(self):
        outcome = self._screen({})
        kept_ids = {chunk.chunk_id for chunk in outcome.kept}
        required_ids = {c.chunk_id for c in outcome.always_kept if c.required}
        self.assertTrue(required_ids)
        self.assertTrue(required_ids <= kept_ids)

    def test_a_clearly_irrelevant_candidate_is_excluded(self):
        candidates = [c for c in mark_bundle(bundle()) if not c.required]
        outcome = self._screen(answers_for(candidates))
        self.assertEqual(
            {item["chunk_id"] for item in outcome.excluded_soft},
            {chunk.chunk_id for chunk in candidates},
        )
        kept_ids = {chunk.chunk_id for chunk in outcome.kept}
        for chunk in candidates:
            self.assertNotIn(chunk.chunk_id, kept_ids)

    def test_a_conflict_escalates_and_is_still_kept(self):
        candidates = [c for c in mark_bundle(bundle()) if not c.required]
        outcome = self._screen(answers_for(candidates, values=(0.9, 0.9, 0.9, 0.05)))
        self.assertEqual(len(outcome.conflicts), len(candidates))
        self.assertTrue({c.chunk_id for c in outcome.conflicts}
                        <= {c.chunk_id for c in outcome.kept})

    def test_a_missing_answer_keeps_the_chunk_as_uncertain(self):
        outcome = self._screen({})
        candidates = [c for c in mark_bundle(bundle()) if not c.required]
        self.assertEqual({c.chunk_id for c in outcome.uncertain},
                         {c.chunk_id for c in candidates})
        excluded = {item["chunk_id"] for item in outcome.excluded_soft}
        self.assertEqual(excluded, set())

    def test_a_failed_request_never_excludes_anything(self):
        candidates = [c for c in mark_bundle(bundle()) if not c.required]
        transport = FakeTransport(responses=[
            TransportResponse(500, "boom", {}) for _ in range(3)
        ])
        client = JevClient(transport, environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None)
        outcome = screen_candidates(
            run_id=RUN_ID, policy=self.policy, artifact_type="script",
            task_description="x", brief="y", bundle=bundle(),
            config=self.config, client=client,
        )
        self.assertEqual(outcome.excluded_soft, ())
        self.assertTrue({c.chunk_id for c in candidates}
                        <= {c.chunk_id for c in outcome.kept})
        self.assertTrue({c.chunk_id for c in candidates}
                        <= {c.chunk_id for c in outcome.uncertain})

    def test_an_unbandable_answer_keeps_its_own_item_uncertain(self):
        # A choice answer to a noul question is a well-formed answer payload, so
        # it survives the result contract; it just cannot be placed in a band.
        # The batch is already paid for, so that chunk has to stay kept and
        # flagged while its siblings still route normally.
        candidates = [c for c in mark_bundle(bundle()) if not c.required]
        answers = answers_for(candidates)
        answers[f"{candidates[0].chunk_id}::relevant"] = {
            "type": "choice", "choice": "yes",
            "probabilities": {"yes": 1.0}, "confidence": 1.0,
        }
        outcome = self._screen(answers)
        self.assertIn(candidates[0].chunk_id,
                      {chunk.chunk_id for chunk in outcome.kept})
        self.assertIn(candidates[0].chunk_id,
                      {chunk.chunk_id for chunk in outcome.uncertain})
        self.assertEqual({item["chunk_id"] for item in outcome.excluded_soft},
                         {chunk.chunk_id for chunk in candidates[1:]})

    def test_no_candidates_means_no_request_at_all(self):
        transport = FakeTransport()
        client = JevClient(transport, environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None)
        outcome = screen_candidates(
            run_id=RUN_ID, policy=self.policy, artifact_type="not_an_artifact",
            task_description="x", brief="y", bundle=bundle(),
            config=self.config, client=client,
        )
        self.assertEqual(transport.calls, [])
        self.assertEqual(outcome.results, ())
        self.assertEqual(outcome.candidate_count, 0)

    def test_an_over_budget_batch_is_never_dispatched(self):
        # Hard constraints are never dropped to make room, so the gap has to be
        # reported before the upload path is paid for: the whole screening stops
        # with the budget error and the transport never sees a request.
        policy = load_policy(default_policy_path())
        policy["operations"][OPERATION]["max_items_per_request"] = 64
        bloated = {
            "items": (
                dict(
                    evidence(),
                    content=WORLDVIEW_BODY
                    + "\n## 场景清单\n\n"
                    + "迈尔斯在森林里散步。" * 4000,
                ),
            ),
            "warnings": (),
            "offline": False,
            "fetched_at": "2026-09-23T10:30:00+08:00",
        }
        transport = FakeTransport()
        client = JevClient(transport, environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None)
        with self.assertRaises(ContextBudgetError):
            screen_candidates(
                run_id=RUN_ID, policy=policy, artifact_type="script",
                task_description="起草第 5 页", brief="分享主题",
                bundle=bloated,
                config=RunnerConfig(run_dir=self.run_dir, policy=policy),
                client=client,
            )
        self.assertEqual(transport.calls, [])

    def test_an_exclusion_survives_an_answer_that_carries_no_probability(self):
        # A hand-authored policy may exclude on a subset of the questions while
        # the same response carries a contract-valid choice answer to another
        # one. The batch is already paid for, so the exclusion is recorded with
        # the probabilities it does have instead of raising after the call.
        policy = load_policy(default_policy_path())
        routing = policy["operations"][OPERATION]["routing"]
        exclusion = next(
            rule for rule in routing["rules"] if rule["route"] == "exclude_soft"
        )
        exclusion["all_of"] = [
            condition for condition in exclusion["all_of"]
            if condition["question_id"] != "contradicts_task_assumption"
        ]
        routing["rules"] = [exclusion]
        candidates = [c for c in mark_bundle(bundle()) if not c.required]
        answers = answers_for(candidates)
        answers[f"{candidates[0].chunk_id}::contradicts_task_assumption"] = {
            "type": "choice", "choice": "yes",
            "probabilities": {"yes": 1.0}, "confidence": 1.0,
        }
        outcome = self._screen(answers, policy=policy)
        recorded = {item["chunk_id"]: item for item in outcome.excluded_soft}
        self.assertIn(candidates[0].chunk_id, recorded)
        self.assertIn("relevant", recorded[candidates[0].chunk_id]["probabilities"])
        self.assertNotIn(
            "contradicts_task_assumption",
            recorded[candidates[0].chunk_id]["probabilities"],
        )
        self.assertEqual({item["chunk_id"] for item in outcome.excluded_soft},
                         {chunk.chunk_id for chunk in candidates})

    def test_a_failure_in_one_batch_does_not_change_another_batch_route(self):
        # Clearing is per item. A batch that failed leaves its own chunks
        # uncertain and kept; it can neither drop a chunk another batch already
        # routed nor keep one that batch had clearly ruled out.
        candidates = [c for c in mark_bundle(bundle()) if not c.required]
        self.assertGreaterEqual(len(candidates), 2)
        policy = load_policy(default_policy_path())
        policy["operations"]["knowledge_relevance"]["max_items_per_request"] = 1
        responses = [TransportResponse(200, json.dumps({
            "model": "jev-1.13.0",
            "answers": answers_for(candidates[:1]),
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }), {})] + [TransportResponse(500, "boom", {}) for _ in range(6)]
        outcome = self._screen(
            answers_for(candidates), responses=responses, policy=policy
        )
        excluded = {item["chunk_id"] for item in outcome.excluded_soft}
        uncertain = {chunk.chunk_id for chunk in outcome.uncertain}
        self.assertEqual(excluded, {candidates[0].chunk_id})
        self.assertEqual(uncertain, {c.chunk_id for c in candidates[1:]})
        kept = {chunk.chunk_id for chunk in outcome.kept}
        self.assertTrue({c.chunk_id for c in candidates[1:]} <= kept)

    def test_two_identical_chunks_receive_the_same_route(self):
        # Copy-pasted sections are common in hand-edited pages. A route follows
        # the answers for that chunk, never its position in the batch, so
        # identical text can never end up half kept and half excluded.
        twin = dict(evidence(key=TWIN_KEY))
        duplicated = {
            "items": (evidence(), twin),
            "warnings": (),
            "offline": False,
            "fetched_at": "2026-09-23T10:30:00+08:00",
        }
        candidates = [c for c in mark_bundle(duplicated) if not c.required]
        originals = [c for c in candidates if c.key == KEY]
        copies = [c for c in candidates if c.key != KEY]
        self.assertTrue(originals and len(originals) == len(copies))
        outcome = self._screen(answers_for(candidates), bundle_override=duplicated)
        excluded = {item["chunk_id"] for item in outcome.excluded_soft}
        for original, copy in zip(originals, copies):
            self.assertEqual(
                original.chunk_id in excluded, copy.chunk_id in excluded
            )
        self.assertEqual(excluded, {chunk.chunk_id for chunk in candidates})


class RevisionVectorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)
        self.policy = load_policy(default_policy_path())
        self.config = RunnerConfig(run_dir=self.run_dir, policy=self.policy)

    def tearDown(self):
        self._tmp.cleanup()

    def _screen_all(self):
        candidates = [c for c in mark_bundle(bundle()) if not c.required]
        body = json.dumps({"model": "jev-1.13.0",
                           "answers": answers_for(candidates),
                           "usage": {"input_tokens": 10, "output_tokens": 2}})
        client = JevClient(FakeTransport(responses=[TransportResponse(200, body, {})]),
                           environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None)
        return screen_candidates(
            run_id=RUN_ID, policy=self.policy, artifact_type="script",
            task_description="x", brief="y", bundle=bundle(),
            config=self.config, client=client,
        )

    def test_the_dependency_bundle_keeps_the_unfiltered_version_vector(self):
        original = bundle()
        kept = dependency_bundle(original)
        self.assertEqual(kept, original)

    def test_the_filtered_bundle_keeps_every_page_when_something_survives(self):
        outcome = self._screen_all()
        filtered = filtered_bundle(bundle(), outcome)
        self.assertEqual(
            [(item["key"], item["revision_id"], item["source_revisions"])
             for item in filtered["items"]],
            [(KEY, 17, {"node-a": "17"})],
        )

    def test_the_filtered_bundle_drops_the_page_body_of_excluded_chunks(self):
        outcome = self._screen_all()
        filtered = filtered_bundle(bundle(), outcome)
        for item in filtered["items"]:
            self.assertNotIn("| 森林 |", item["content"])

    def test_the_filtered_bundle_round_trips_through_the_loader_codec(self):
        from load_knowledge import bundle_from_dict

        filtered = filtered_bundle(bundle(), self._screen_all())
        rebuilt = bundle_from_dict(filtered)
        self.assertEqual(rebuilt.items[0].revision_id, 17)
        self.assertEqual(rebuilt.items[0].source_revisions, {"node-a": "17"})

    def test_excluded_chunks_are_recorded_with_their_probabilities(self):
        outcome = self._screen_all()
        self.assertTrue(outcome.excluded_soft)
        for entry in outcome.excluded_soft:
            with self.subTest(chunk=entry["chunk_id"]):
                self.assertEqual(entry["route"], "exclude_soft")
                self.assertIn("relevant", entry["probabilities"])


if __name__ == "__main__":
    unittest.main()
