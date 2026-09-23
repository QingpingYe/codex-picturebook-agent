"""Phase 2 integration: the screening invariants that must never regress."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "skills" / "jev-decision-runtime" / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "knowledge-loader" / "scripts"))

from decision_contract import default_policy_path, load_policy  # noqa: E402
from jev_client import API_KEY_ENV, FakeTransport, JevClient, TransportResponse  # noqa: E402
from jev_runner import RunnerConfig  # noqa: E402
from load_knowledge import bundle_from_dict  # noqa: E402
from dependencies import build_dependency_record  # noqa: E402
from collision import check_collisions  # noqa: E402
from relevance import (  # noqa: E402
    dependency_bundle,
    filtered_bundle,
    screen_candidates,
)
from required_marking import mark_bundle  # noqa: E402

RUN_ID = "20260923-integration-0001"
WORLDVIEW_KEY = "海外绘本/小老鼠迈尔斯/worldview"
CORRECTIONS_KEY = "海外绘本/小老鼠迈尔斯/corrections"

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

CORRECTIONS_BODY = """# 纠正台账

## 强制性禁止条目

绝不可把解决问题的方式写成"变勇敢了"。

## 迭代历史

- 2026-09-01：新增一条禁止词。
"""


def item(key, body):
    return {
        "key": key,
        "doc_token": f"doxcn{abs(hash(key)) % 10**6}",
        "revision_id": 17,
        "title": key,
        "content": body,
        "source_revisions": {"node-a": "17"},
        "status": "published",
        "index_synced": True,
    }


def bundle():
    return {
        "items": (item(WORLDVIEW_KEY, WORLDVIEW_BODY), item(CORRECTIONS_KEY, CORRECTIONS_BODY)),
        "warnings": (),
        "offline": False,
        "fetched_at": "2026-09-23T10:30:00+08:00",
    }


def noul(value):
    return {"type": "noul", "noul": value}


IRRELEVANT = (0.02, 0.02, 0.02, 0.02)


class Phase2IntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)
        self.policy = load_policy(default_policy_path())
        self.config = RunnerConfig(run_dir=self.run_dir, policy=self.policy)

    def tearDown(self):
        self._tmp.cleanup()

    def _screen(self, candidates, values=IRRELEVANT, status_code=200):
        answers = {}
        for chunk in candidates:
            for question_id, value in zip(
                ("relevant", "usable_evidence", "contradicts_task_assumption",
                 "instruction_like_content"),
                values,
            ):
                answers[f"{chunk.chunk_id}::{question_id}"] = noul(value)
        body = json.dumps({"model": "jev-1.13.0", "answers": answers,
                           "usage": {"input_tokens": 200, "output_tokens": 20}})
        client = JevClient(
            FakeTransport(responses=[TransportResponse(status_code, body, {})]),
            environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None,
        )
        return screen_candidates(
            run_id=RUN_ID, policy=self.policy, artifact_type="script",
            task_description="起草第 5 页", brief="分享主题，3-6 岁",
            bundle=bundle(), config=self.config, client=client,
        )

    def candidates(self):
        return [chunk for chunk in mark_bundle(bundle()) if not chunk.required]

    def test_a_hard_constraint_survives_every_irrelevant_verdict(self):
        outcome = self._screen(self.candidates(), values=(0.0, 0.0, 0.0, 0.0))
        required_ids = {chunk.chunk_id for chunk in mark_bundle(bundle()) if chunk.required}
        self.assertTrue(required_ids)
        self.assertTrue(required_ids <= {chunk.chunk_id for chunk in outcome.kept})

    def test_the_red_line_section_is_required_and_stays_in_the_context(self):
        filtered = filtered_bundle(bundle(), self._screen(self.candidates(),
                                                          values=(0.0, 0.0, 0.0, 0.0)))
        worldview = next(i for i in filtered["items"] if i["key"] == WORLDVIEW_KEY)
        self.assertIn("创作红线不变量", worldview["content"])
        self.assertIn("不能飞行", worldview["content"])

    def test_a_conflict_is_escalated_but_the_chunk_is_not_removed(self):
        outcome = self._screen(self.candidates(), values=(0.9, 0.9, 0.9, 0.02))
        self.assertTrue(outcome.conflicts)
        conflict_ids = {chunk.chunk_id for chunk in outcome.conflicts}
        self.assertTrue(conflict_ids <= {chunk.chunk_id for chunk in outcome.kept})
        self.assertEqual(
            [route for route in outcome.routes if route["route"] == "escalate_llm"],
            [route for route in outcome.routes if route.get("label") == "conflict"],
        )

    def test_an_excluded_chunk_is_still_in_the_authority_lock_record(self):
        original = bundle()
        outcome = self._screen(self.candidates(), values=(0.0, 0.0, 0.0, 0.0))
        self.assertTrue(outcome.excluded_soft)
        record = build_dependency_record(
            bundle_from_dict(dependency_bundle(original)), "picturebook/script_v1.md", "script"
        )
        self.assertEqual(
            {entry["key"] for entry in record.evidence},
            {WORLDVIEW_KEY, CORRECTIONS_KEY},
        )
        self.assertEqual(
            [entry["revision_id"] for entry in record.evidence], [17, 17]
        )

    def test_the_reduced_context_never_touches_the_version_vector(self):
        original = bundle()
        filtered = filtered_bundle(original, self._screen(
            self.candidates(), values=(0.0, 0.0, 0.0, 0.0)
        ))
        for before, after in zip(original["items"], filtered["items"]):
            with self.subTest(key=before["key"]):
                self.assertEqual(after["revision_id"], before["revision_id"])
                self.assertEqual(after["source_revisions"], before["source_revisions"])
                self.assertEqual(after["doc_token"], before["doc_token"])
                self.assertEqual(after["status"], before["status"])
                self.assertEqual(after["index_synced"], before["index_synced"])

    def test_the_reduced_context_is_still_a_valid_bundle(self):
        filtered = filtered_bundle(bundle(), self._screen(
            self.candidates(), values=(0.0, 0.0, 0.0, 0.0)
        ))
        rebuilt = bundle_from_dict(filtered)
        self.assertEqual(rebuilt.offline, False)
        self.assertEqual(rebuilt.fetched_at, "2026-09-23T10:30:00+08:00")
        self.assertTrue(all(item.index_synced for item in rebuilt.items))

    def test_the_reduced_context_still_supports_collision_checks(self):
        filtered = filtered_bundle(bundle(), self._screen(
            self.candidates(), values=(0.0, 0.0, 0.0, 0.0)
        ))
        hits = check_collisions("迈尔斯不能飞行。", filtered, ("不能飞行",))
        self.assertTrue(hits)
        self.assertEqual(hits[0].key, WORLDVIEW_KEY)
        # The scene list was excluded, so a term only it carries no longer
        # collides: the reduced bundle is what the plain LLM actually sees.
        self.assertEqual(
            check_collisions("小老鼠迈尔斯去了森林。", filtered, ("森林",)), ()
        )

    def test_an_incomplete_response_excludes_nothing(self):
        candidates = self.candidates()
        body = json.dumps({"model": "jev-1.13.0", "answers": {}, "usage": {}})
        client = JevClient(
            FakeTransport(responses=[TransportResponse(200, body, {})]),
            environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None,
        )
        outcome = screen_candidates(
            run_id=RUN_ID, policy=self.policy, artifact_type="script",
            task_description="x", brief="y", bundle=bundle(),
            config=self.config, client=client,
        )
        self.assertEqual(outcome.results[0]["status"], "failed")
        self.assertEqual(outcome.excluded_soft, ())
        self.assertEqual({chunk.chunk_id for chunk in candidates},
                         {chunk.chunk_id for chunk in outcome.uncertain})

    def test_a_transport_failure_excludes_nothing(self):
        candidates = self.candidates()
        client = JevClient(
            FakeTransport(error=__import__("jev_client").JevTransportFailure("refused")),
            environ={API_KEY_ENV: "sk-abc"}, sleep=lambda _: None,
        )
        outcome = screen_candidates(
            run_id=RUN_ID, policy=self.policy, artifact_type="script",
            task_description="x", brief="y", bundle=bundle(),
            config=self.config, client=client,
        )
        self.assertEqual(outcome.excluded_soft, ())
        self.assertEqual({chunk.chunk_id for chunk in candidates},
                         {chunk.chunk_id for chunk in outcome.uncertain})

    def test_a_missing_key_excludes_nothing_and_writes_no_result(self):
        candidates = self.candidates()
        client = JevClient(FakeTransport(), environ={}, sleep=lambda _: None)
        outcome = screen_candidates(
            run_id=RUN_ID, policy=self.policy, artifact_type="script",
            task_description="x", brief="y", bundle=bundle(),
            config=self.config, client=client,
        )
        self.assertEqual(outcome.excluded_soft, ())
        self.assertEqual(outcome.results[0]["status"], "waiting_for_jev_key")
        self.assertFalse(list((self.run_dir / "jev").rglob("result.json")))


if __name__ == "__main__":
    unittest.main()
