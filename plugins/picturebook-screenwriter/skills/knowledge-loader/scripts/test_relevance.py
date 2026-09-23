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
from jev_client import (  # noqa: E402
    API_KEY_ENV,
    CallOutcome,
    FakeTransport,
    JevClient,
    JevTransportOutcomeUnknown,
    TransportResponse,
)
from jev_runner import (  # noqa: E402
    LeaseHeld,
    RunnerConfig,
    acquire_lease,
    build_pending_call,
    operation_id_from_request,
    pending_path,
    pending_operation_ids,
    read_json,
    request_path,
    result_path,
    resume_operation,
    write_atomic,
)
import relevance  # noqa: E402
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

# 200 bodies that carry no readable answer envelope. The screening loop calls
# the runner once per batch without a guard of its own, so a body the runner
# cannot settle on would surface as a traceback from the whole operation.
MALFORMED_ENVELOPES = (
    "[]",
    '"just text"',
    "42",
    "null",
    "true",
    '{"model": "jev-1.13.0", "answers": [[1, 2]]}',
    '{"model": "jev-1.13.0", "answers": 5}',
    '{"model": "jev-1.13.0", "answers": "nope"}',
)

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


# A page with four soft chunks and no hard-constraint section, so a cap of two
# items per request turns it into two batches: the multi-batch shape a real
# authority bundle produces.
MULTI_KEY = "海外绘本/小老鼠迈尔斯/characters"
MULTI_BODY = """# 角色总表

## 主角小传

迈尔斯是只小老鼠。

## 配角清单

- 森林松鼠

## 关系图

迈尔斯与松鼠是邻居。
"""


def multi_bundle():
    return {
        "items": (evidence(key=MULTI_KEY, body=MULTI_BODY),),
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


class ScriptedClient:
    """Scripted outcomes, so one run can mix a failure with a wait."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def call(self, request, *, before_dispatch=None):
        if before_dispatch is not None:
            before_dispatch()
        self.calls.append(request)
        return self.outcomes.pop(0)


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

    def _screen(self, answers, responses=None, bundle_override=None, policy=None,
                body=None):
        if body is None:
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

    def test_a_malformed_envelope_keeps_every_candidate_uncertain(self):
        # One unguarded batch would abort the whole screening, because the loop
        # calls the runner without a guard of its own. A 200 body the runner
        # cannot read has to settle that batch as a terminal failure, leaving
        # every candidate of the batch kept and flagged for review.
        candidates = [c for c in mark_bundle(bundle()) if not c.required]
        self.assertTrue(candidates)
        for body in MALFORMED_ENVELOPES:
            with self.subTest(body=body):
                outcome = self._screen({}, body=body)
                self.assertEqual([result["status"] for result in outcome.results],
                                 ["failed"])
                self.assertEqual([result["error_class"] for result in outcome.results],
                                 ["incomplete_response"])
                self.assertEqual(outcome.excluded_soft, ())
                self.assertEqual({chunk.chunk_id for chunk in outcome.uncertain},
                                 {chunk.chunk_id for chunk in candidates})
                self.assertTrue({chunk.chunk_id for chunk in candidates}
                                <= {chunk.chunk_id for chunk in outcome.kept})

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

    def test_a_repeated_page_key_is_kept_instead_of_screened(self):
        # `chunk_id` is `<page key>#<ordinal>`, so two pages that share a key
        # share every question id: the request and its state would carry one of
        # the two texts and both pages would be routed on it. Nothing about that
        # is screenable, so the collision is kept and flagged rather than paid
        # for and decided on another page's evidence.
        first = evidence(body="# 世界观总纲\n\n## 核心价值主张\n\n勇气。\n")
        second = dict(evidence(body="# 世界观总纲\n\n## 场景清单\n\n森林。\n"))
        repeated = {
            "items": (first, second),
            "warnings": (),
            "offline": False,
            "fetched_at": "2026-09-23T10:30:00+08:00",
        }
        candidates = [c for c in mark_bundle(repeated) if not c.required]
        self.assertTrue(candidates)
        self.assertEqual(len({c.chunk_id for c in candidates}), 2)
        transport = FakeTransport()
        outcome = screen_candidates(
            run_id=RUN_ID, policy=self.policy, artifact_type="script",
            task_description="起草第 5 页", brief="分享主题",
            bundle=repeated,
            config=RunnerConfig(run_dir=self.run_dir, policy=self.policy),
            client=JevClient(transport, environ={API_KEY_ENV: "sk-abc"},
                             sleep=lambda _: None),
        )
        self.assertEqual(transport.calls, [])
        self.assertEqual(outcome.results, ())
        self.assertEqual(outcome.excluded_soft, ())
        self.assertEqual({chunk.chunk_id for chunk in outcome.uncertain},
                         {chunk.chunk_id for chunk in candidates})
        self.assertEqual({chunk.chunk_id for chunk in outcome.kept},
                         {chunk.chunk_id for chunk in candidates})


class MultiBatchResumeTests(unittest.TestCase):
    """A bundle that needs more than one request must still be resumable.

    `resume_operation` refuses a run that holds more than one pending call, so
    a screen that opened every batch at once could never be continued: the
    caller configures the key and the documented flow stops dead. One call is
    therefore open at a time, and whatever already settled is reused.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)
        self.policy = load_policy(default_policy_path())
        # Two chunks per request, so this bundle needs two batches.
        self.policy["operations"][OPERATION]["max_items_per_request"] = 2
        self.bundle = multi_bundle()
        self.candidates = [
            chunk for chunk in mark_bundle(self.bundle) if not chunk.required
        ]

    def tearDown(self):
        self._tmp.cleanup()

    def _operation_id(self, batch_index):
        return f"{RUN_ID}-{OPERATION}-batch-{batch_index:03d}"

    def _batch_body(self, batch, values=(0.02, 0.02, 0.02, 0.02)):
        return TransportResponse(200, json.dumps({
            "model": "jev-1.13.0",
            "answers": answers_for(batch, values),
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }), {})

    def _client(self, transport, key="sk-abc"):
        return JevClient(
            transport,
            environ={API_KEY_ENV: key} if key else {},
            sleep=lambda _: None,
        )

    def _config(self, policy=None):
        return RunnerConfig(self.run_dir, policy or self.policy)

    def _screen(self, client, policy=None, task="起草第 5 页", brief="分享主题"):
        return screen_candidates(
            run_id=RUN_ID, policy=policy or self.policy, artifact_type="script",
            task_description=task, brief=brief, bundle=self.bundle,
            config=self._config(policy), client=client,
        )

    def _batches(self):
        return plan_batches(self.candidates, 2)

    def test_a_keyless_multi_batch_run_leaves_exactly_one_pending_call(self):
        transport = FakeTransport()
        outcome = self._screen(self._client(transport, key=None))

        self.assertEqual(transport.calls, [])
        self.assertEqual(pending_operation_ids(self.run_dir), [self._operation_id(1)])
        self.assertEqual([result["status"] for result in outcome.results],
                         ["waiting_for_jev_key"])
        # No batch was drawn on beyond the one that is waiting, so the whole
        # bundle stays kept and uncertain and nothing is excluded.
        self.assertEqual(outcome.excluded_soft, ())
        self.assertEqual(
            {chunk.chunk_id for chunk in outcome.uncertain},
            {chunk.chunk_id for chunk in self.candidates},
        )
        self.assertTrue(
            {chunk.chunk_id for chunk in self.candidates}
            <= {chunk.chunk_id for chunk in outcome.kept}
        )
        self.assertFalse(list((self.run_dir / "jev").rglob("result.json")))

    def test_continuing_after_the_key_is_configured_screens_each_batch_once(self):
        self._screen(self._client(FakeTransport(), key=None))
        pending_id = pending_operation_ids(self.run_dir)[0]
        stored_request = read_json(request_path(self.run_dir, pending_id))

        resume_transport = FakeTransport(
            responses=[self._batch_body(self._batches()[0])]
        )
        resumed = resume_operation(
            self._config(), self._client(resume_transport),
            stored_request["context_refs"],
        )
        self.assertEqual(resumed["status"], "succeeded")
        self.assertEqual(len(resume_transport.calls), 1)

        transport = FakeTransport(responses=[self._batch_body(self._batches()[1])])
        outcome = self._screen(self._client(transport))

        # Only the batch that was never dispatched is paid for now; the settled
        # one is routed from its stored result instead.
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual([result["status"] for result in outcome.results],
                         ["succeeded", "succeeded"])
        self.assertEqual(
            {item["chunk_id"] for item in outcome.excluded_soft},
            {chunk.chunk_id for chunk in self.candidates},
        )
        self.assertEqual(pending_operation_ids(self.run_dir), [])
        for batch_index in (1, 2):
            self.assertTrue(
                (self.run_dir / "jev" / self._operation_id(batch_index)
                 / "result.json").is_file()
            )

    def test_a_settled_batch_is_never_paid_for_twice(self):
        transport = FakeTransport(
            responses=[self._batch_body(batch) for batch in self._batches()]
        )
        first = self._screen(self._client(transport))
        self.assertEqual(len(transport.calls), 2)

        # Nothing is left to dispatch, so a scripted transport with no response
        # left would raise if the screen opened another call.
        second_transport = FakeTransport()
        second = self._screen(self._client(second_transport))
        self.assertEqual(second_transport.calls, [])
        self.assertEqual([result["status"] for result in second.results],
                         ["succeeded", "succeeded"])
        self.assertEqual(
            {item["chunk_id"] for item in second.excluded_soft},
            {item["chunk_id"] for item in first.excluded_soft},
        )

    def test_an_attempt_that_may_have_been_billed_is_never_sent_again(self):
        transport = FakeTransport(error=JevTransportOutcomeUnknown("timeout"))
        outcome = self._screen(self._client(transport))
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual([result["status"] for result in outcome.results],
                         ["outcome_unknown"])
        self.assertEqual(pending_operation_ids(self.run_dir), [self._operation_id(1)])

        # A call that may already have been billed is not re-sent without the
        # user's explicit consent, so this run stops where the open call is and
        # the batch it never reached stays kept and uncertain.
        second_transport = FakeTransport()
        second = self._screen(self._client(second_transport))
        self.assertEqual(second_transport.calls, [])
        self.assertEqual(second.excluded_soft, ())
        self.assertEqual(
            {chunk.chunk_id for chunk in second.uncertain},
            {chunk.chunk_id for chunk in self.candidates},
        )
        self.assertEqual(
            [entry["reason"] for entry in second.blocked_records], ["open_attempt"]
        )

    def test_an_unreadable_pending_record_is_not_sent_again(self):
        self._screen(self._client(FakeTransport(), key=None))
        pending = self.run_dir / "jev" / self._operation_id(1) / "pending.json"
        pending.write_text("not json", encoding="utf-8")

        # A record that cannot be read cannot rule out a billed attempt either.
        transport = FakeTransport()
        outcome = self._screen(self._client(transport))
        self.assertEqual(transport.calls, [])
        self.assertEqual(outcome.excluded_soft, ())
        self.assertEqual(
            {chunk.chunk_id for chunk in outcome.uncertain},
            {chunk.chunk_id for chunk in self.candidates},
        )
        self.assertEqual(
            [entry["reason"] for entry in outcome.blocked_records],
            ["unreadable_pending"],
        )

    def test_a_failed_batch_does_not_stop_the_loop_and_is_retried(self):
        transport = FakeTransport(responses=[
            TransportResponse(500, "boom", {}) for _ in self._batches()
        ])
        first = self._screen(self._client(transport))
        self.assertEqual([result["status"] for result in first.results],
                         ["failed", "failed"])
        self.assertEqual(first.excluded_soft, ())
        self.assertEqual(
            {chunk.chunk_id for chunk in first.uncertain},
            {chunk.chunk_id for chunk in self.candidates},
        )

        retry = FakeTransport(
            responses=[self._batch_body(batch) for batch in self._batches()]
        )
        second = self._screen(self._client(retry))
        self.assertEqual(len(retry.calls), 2)
        self.assertEqual(
            {item["chunk_id"] for item in second.excluded_soft},
            {chunk.chunk_id for chunk in self.candidates},
        )

    def test_a_failed_batch_leaves_no_call_to_continue(self):
        transport = FakeTransport(responses=[
            TransportResponse(500, "boom", {}) for _ in self._batches()
        ])
        outcome = self._screen(self._client(transport))
        self.assertEqual(len(transport.calls), 2)

        # A settled failure is not a call to continue. Leaving its pending
        # record behind would make a later wait state unresumable, because
        # `resume_operation` refuses a run that holds more than one.
        self.assertEqual(pending_operation_ids(self.run_dir), [])
        self.assertEqual(outcome.excluded_soft, ())

    def test_a_failure_before_a_waiting_batch_leaves_one_resumable_call(self):
        client = ScriptedClient([
            CallOutcome("failed", 500, None, "transport_error", 1),
            CallOutcome("waiting_for_jev_key", None, None, None, 0),
        ])
        first = self._screen(client)
        self.assertEqual([result["status"] for result in first.results],
                         ["failed", "waiting_for_jev_key"])

        # The batch that is still waiting holds the one call to continue with,
        # so the documented "configure the key, then continue" step works.
        self.assertEqual(pending_operation_ids(self.run_dir), [self._operation_id(2)])
        stored = read_json(request_path(self.run_dir, self._operation_id(2)))
        resumed = resume_operation(
            self._config(),
            self._client(FakeTransport(responses=[self._batch_body(self._batches()[1])])),
            stored["context_refs"],
        )
        self.assertEqual(resumed["status"], "succeeded")
        self.assertEqual(pending_operation_ids(self.run_dir), [])

    def test_a_held_lease_stops_the_screen_from_dispatching(self):
        other = RunnerConfig(self.run_dir, self.policy, holder="another-runner")
        acquire_lease(other, self._operation_id(1))

        transport = FakeTransport(
            responses=[self._batch_body(batch) for batch in self._batches()]
        )
        with self.assertRaises(LeaseHeld):
            self._screen(self._client(transport))
        self.assertEqual(transport.calls, [])

    def test_an_unreadable_request_record_is_not_sent_again(self):
        self._screen(
            self._client(FakeTransport(error=JevTransportOutcomeUnknown("timeout")))
        )
        request_path(self.run_dir, self._operation_id(1)).write_text(
            "not json", encoding="utf-8"
        )

        transport = FakeTransport(
            responses=[self._batch_body(batch) for batch in self._batches()]
        )
        outcome = self._screen(self._client(transport))

        # A record that cannot be read cannot rule out a billed attempt either,
        # so this batch is kept rather than sent again, and the rest of the
        # bundle stays unscreened behind it.
        self.assertEqual(transport.calls, [])
        self.assertEqual(outcome.excluded_soft, ())
        self.assertEqual(
            {chunk.chunk_id for chunk in outcome.uncertain},
            {chunk.chunk_id for chunk in self.candidates},
        )
        self.assertEqual(
            [entry["reason"] for entry in outcome.blocked_records],
            ["unreadable_request"],
        )

    def test_a_stored_result_that_cannot_be_read_is_not_routed_from(self):
        transport = FakeTransport(
            responses=[self._batch_body(batch) for batch in self._batches()]
        )
        self._screen(self._client(transport))
        path = result_path(self.run_dir, self._operation_id(1))
        stored = read_json(path)
        stored["answers"] = "nope"
        path.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")

        second_transport = FakeTransport()
        outcome = self._screen(self._client(second_transport))

        # A record that does not satisfy the result contract is neither routed
        # from nor paid for again: the batch is kept and flagged instead.
        self.assertEqual(second_transport.calls, [])
        self.assertEqual(outcome.excluded_soft, ())
        self.assertEqual(
            {chunk.chunk_id for chunk in outcome.uncertain},
            {chunk.chunk_id for chunk in self.candidates},
        )
        self.assertEqual(
            [entry["reason"] for entry in outcome.blocked_records],
            ["unreadable_result"],
        )
        self.assertEqual(
            [entry["path"] for entry in outcome.blocked_records], [str(path)]
        )

    def test_a_stored_result_with_unreadable_answer_ids_is_not_routed_from(self):
        transport = FakeTransport(
            responses=[self._batch_body(batch) for batch in self._batches()]
        )
        self._screen(self._client(transport))
        path = result_path(self.run_dir, self._operation_id(1))
        stored = read_json(path)
        stored["answers"] = {"no-item-separator": noul(0.02)}
        path.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")

        second_transport = FakeTransport()
        outcome = self._screen(self._client(second_transport))
        self.assertEqual(second_transport.calls, [])
        self.assertEqual(outcome.excluded_soft, ())

    def test_a_result_written_for_another_request_is_not_routed_from(self):
        # `result.json` is only rewritten by a dispatch that succeeds while
        # `request.json` is rewritten by every dispatch, so an edit to a page
        # plus one failed dispatch leaves the edited request beside the earlier
        # answer. Routing the edit on those verdicts would drop (or escalate)
        # its chunks on evidence that is not the evidence in hand, and the
        # report would still say `succeeded`.
        first_transport = FakeTransport(responses=[
            self._batch_body(batch, values=(0.02, 0.02, 0.02, 0.02))
            for batch in self._batches()
        ])
        self._screen(self._client(first_transport))
        self.assertEqual(len(first_transport.calls), 2)

        failed = FakeTransport(responses=[
            TransportResponse(500, "boom", {}) for _ in self._batches()
        ])
        second = self._screen(self._client(failed), brief="换一个主题")
        self.assertEqual(len(failed.calls), 2)
        self.assertEqual([result["status"] for result in second.results],
                         ["failed", "failed"])

        # The screen runs again for the edited page: the stored result answers
        # the previous request, so nothing is paid for, nothing is excluded,
        # and the record that stopped the batch is named.
        third_transport = FakeTransport()
        third = self._screen(self._client(third_transport), brief="换一个主题")

        self.assertEqual(third_transport.calls, [])
        self.assertEqual(third.results, ())
        self.assertEqual(third.excluded_soft, ())
        self.assertEqual(
            {chunk.chunk_id for chunk in third.uncertain},
            {chunk.chunk_id for chunk in self.candidates},
        )
        self.assertEqual(
            [entry["reason"] for entry in third.blocked_records], ["stale_result"]
        )
        self.assertEqual(
            [entry["operation_id"] for entry in third.blocked_records],
            [self._operation_id(1)],
        )

    def test_a_stored_result_without_its_request_identity_is_not_routed_from(self):
        transport = FakeTransport(
            responses=[self._batch_body(batch) for batch in self._batches()]
        )
        self._screen(self._client(transport))
        path = result_path(self.run_dir, self._operation_id(1))
        stored = read_json(path)
        del stored["trace"]
        path.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")

        second_transport = FakeTransport()
        outcome = self._screen(self._client(second_transport))

        # A record that does not say which request it answered is not evidence
        # about this one: it is neither routed from nor paid for again.
        self.assertEqual(second_transport.calls, [])
        self.assertEqual(outcome.excluded_soft, ())
        self.assertEqual(
            [entry["reason"] for entry in outcome.blocked_records],
            ["stale_result"],
        )

    def test_a_second_screens_in_flight_record_survives_the_cleanup(self):
        # The failure cleanup used to run after the lease was released, so a
        # second screen that opened its own attempt for the same batch in that
        # window had its in-flight record deleted under it — losing the only
        # record of a call that may already have been billed. This emulates
        # that interleaving by writing the second screen's record immediately
        # before the cleanup runs.
        original = relevance.discard_failed_pending_call

        def racing_cleanup(request, config):
            foreign = build_pending_call(request)
            foreign["attempt_status"] = "in_flight"
            write_atomic(
                pending_path(self.run_dir, operation_id_from_request(request)),
                foreign,
            )
            return original(request, config)

        relevance.discard_failed_pending_call = racing_cleanup
        try:
            transport = FakeTransport(responses=[
                TransportResponse(500, "boom", {}) for _ in self._batches()
            ])
            self._screen(self._client(transport))
        finally:
            relevance.discard_failed_pending_call = original

        self.assertEqual(
            pending_operation_ids(self.run_dir),
            [self._operation_id(1), self._operation_id(2)],
        )
        for batch_index in (1, 2):
            record = read_json(
                pending_path(self.run_dir, self._operation_id(batch_index))
            )
            self.assertEqual(record["attempt_status"], "in_flight")

    def test_a_changed_bundle_is_screened_again_instead_of_reused(self):
        transport = FakeTransport(
            responses=[self._batch_body(batch) for batch in self._batches()]
        )
        self._screen(self._client(transport))
        self.assertEqual(len(transport.calls), 2)

        # Same run directory, new brief: the stored results were decided on
        # other inputs, so every batch is screened again for the new ones.
        changed = FakeTransport(
            responses=[self._batch_body(batch) for batch in self._batches()]
        )
        self._screen(self._client(changed), brief="换一个主题")
        self.assertEqual(len(changed.calls), 2)

    def test_a_single_batch_bundle_keeps_its_behaviour(self):
        policy = load_policy(default_policy_path())
        transport = FakeTransport(responses=[self._batch_body(self.candidates)])
        outcome = self._screen(self._client(transport), policy=policy)

        self.assertEqual(len(transport.calls), 1)
        self.assertEqual([result["status"] for result in outcome.results],
                         ["succeeded"])
        self.assertEqual(pending_operation_ids(self.run_dir), [])
        self.assertEqual(
            {item["chunk_id"] for item in outcome.excluded_soft},
            {chunk.chunk_id for chunk in self.candidates},
        )


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
