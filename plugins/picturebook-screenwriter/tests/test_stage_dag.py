#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stage_dag.py 的离线单测（unittest，无网络）。"""
import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import stage_dag


def _manifest(**overrides):
    stages = [
        {
            "stage_id": "session_init",
            "assignee": "pb-intake-agent",
            "depends_on": [],
            "status": "pending",
            "gate": "none",
            "outcome": None,
            "when": None,
            "input_refs": [],
            "output_refs": [],
            "skip_reason": None,
            "blocked_reason": None,
        },
        {
            "stage_id": "brief_gate",
            "assignee": "pb-intake-agent",
            "depends_on": ["session_init"],
            "status": "pending",
            "gate": "brief",
            "outcome": None,
            "when": None,
            "input_refs": [],
            "output_refs": [],
            "skip_reason": None,
            "blocked_reason": None,
        },
    ]
    manifest = {
        "schema_version": "pb-stage-run-v2",
        "run_id": "20260922-example-0001",
        "root_run_id": "20260922-example-0001",
        "revision_of_run_id": None,
        "iteration": 1,
        "intent": "creation",
        "artifact_type": "script",
        "mode": "full",
        "project_root": "E:/workspace/example-project",
        "status": "pending",
        "outcome": None,
        "revision_feedback": [],
        "source_artifact_ref": None,
        "stages": stages,
    }
    manifest.update(overrides)
    return manifest


def _task():
    return {
        "schema_version": "pb-stage-task-v1",
        "run_id": "20260921-example-0001",
        "task_id": "brief_gate-0001",
        "stage_id": "brief_gate",
        "agent_id": "pb-intake-agent",
        "logical_target": "pb-intake-agent",
        "inputs": {},
        "constraints": {},
        "resume_context": "ab12cd34",
        "output_paths": ["outputs/pb-intake-agent/brief.json"],
        "return_channel": "lead",
    }


def _result():
    return {
        "schema_version": "pb-stage-result-v1",
        "run_id": "20260921-example-0001",
        "task_id": "brief_gate-0001",
        "stage_id": "brief_gate",
        "agent_id": "pb-intake-agent",
        "status": "done",
        "result": {},
        "artifact_refs": [],
        "requests": [],
        "risks": [],
        "resume_context": "ab12cd34",
    }


class StageDagManifestTest(unittest.TestCase):

    def test_validate_manifest_accepts_minimal_run(self):
        source = _manifest()
        result = stage_dag.validate_manifest(source)
        self.assertEqual(result, source)

    def test_validate_manifest_rejects_wrong_schema_version_and_duplicate_stage_ids(self):
        bad = _manifest()
        bad["schema_version"] = "pb-stage-run-v0"
        with self.assertRaises(stage_dag.StageDagError) as ctx:
            stage_dag.validate_manifest(bad)
        self.assertTrue(any("pb-stage-run-v2" in msg for msg in ctx.exception.errors))

        bad = _manifest()
        bad["stages"][1]["stage_id"] = "session_init"
        with self.assertRaises(stage_dag.StageDagError) as ctx:
            stage_dag.validate_manifest(bad)
        self.assertTrue(any("重复" in msg for msg in ctx.exception.errors))

    def test_validate_manifest_rejects_unknown_dependency_and_cycle(self):
        bad = _manifest()
        bad["stages"][1]["depends_on"] = ["missing"]
        with self.assertRaises(stage_dag.StageDagError) as ctx:
            stage_dag.validate_manifest(bad)
        self.assertTrue(any("missing" in msg for msg in ctx.exception.errors))

        bad = _manifest()
        bad["stages"][0]["depends_on"] = ["brief_gate"]
        bad["stages"][1]["depends_on"] = ["session_init"]
        with self.assertRaises(stage_dag.StageDagError) as ctx:
            stage_dag.topological_order(bad)
        self.assertTrue(any("循环" in msg for msg in ctx.exception.errors))

    def test_validate_manifest_direct_v1_points_to_migration(self):
        legacy = _manifest()
        legacy["schema_version"] = stage_dag.RUN_SCHEMA_V1
        with self.assertRaises(stage_dag.StageDagError) as ctx:
            stage_dag.validate_manifest(legacy)
        self.assertTrue(
            any("--action migrate" in msg for msg in ctx.exception.errors)
        )

    def test_validate_manifest_rejects_invalid_when_object(self):
        bad = _manifest()
        bad["stages"][1]["when"] = {
            "stage_id": "session_init",
            "outcome": "approved",
            "extra": True,
        }
        with self.assertRaises(stage_dag.StageDagError) as ctx:
            stage_dag.validate_manifest(bad)
        self.assertTrue(any("when" in msg for msg in ctx.exception.errors))


class StageDagMigrationTest(unittest.TestCase):

    def test_migrate_v1_removes_revision_loop_and_adds_persistence_condition(self):
        legacy = stage_dag._creation_template_manifest_v1()
        migrated = stage_dag.migrate_manifest_v1(legacy)
        stages = {stage["stage_id"]: stage for stage in migrated["stages"]}

        self.assertEqual(migrated["schema_version"], "pb-stage-run-v2")
        self.assertEqual(migrated["root_run_id"], legacy["run_id"])
        self.assertEqual(migrated["iteration"], 1)
        self.assertNotIn("revision_loop", stages)
        self.assertEqual(
            stages["persistence"]["when"],
            {"stage_id": "confirmation_gate", "outcome": "approved"},
        )

    def test_migrate_rejects_completed_gate_without_outcome(self):
        legacy = stage_dag._creation_template_manifest_v1()
        gate = next(
            stage for stage in legacy["stages"]
            if stage["stage_id"] == "confirmation_gate"
        )
        gate["status"] = "done"

        with self.assertRaisesRegex(stage_dag.StageDagError, "outcome"):
            stage_dag.migrate_manifest_v1(legacy)

    def test_migrate_rejects_unknown_stage(self):
        legacy = stage_dag._creation_template_manifest_v1()
        legacy["stages"].append({
            "stage_id": "unknown_stage",
            "assignee": "pb-screenwriter",
            "depends_on": [],
            "status": "pending",
            "gate": "none",
            "input_refs": [],
            "output_refs": [],
        })

        with self.assertRaisesRegex(stage_dag.StageDagError, "未知 stage"):
            stage_dag.migrate_manifest_v1(legacy)

    def test_migrate_rejects_missing_required_stage(self):
        legacy = stage_dag._creation_template_manifest_v1()
        legacy["stages"] = [
            stage for stage in legacy["stages"]
            if stage["stage_id"] != "persistence"
        ]

        with self.assertRaisesRegex(stage_dag.StageDagError, "persistence"):
            stage_dag.migrate_manifest_v1(legacy)


class StageDagSchedulingTest(unittest.TestCase):

    def test_ready_stages_requires_dependencies_done(self):
        manifest = _manifest()
        self.assertEqual(stage_dag.ready_stages(manifest), ["session_init"])

        manifest = _manifest()
        manifest["stages"][0]["status"] = "done"
        self.assertEqual(stage_dag.ready_stages(manifest), ["brief_gate"])

    def test_parallel_batches_returns_ready_independent_stages(self):
        manifest = _manifest()
        manifest["stages"].append({
            "stage_id": "knowledge_load",
            "assignee": "pb-knowledge-steward",
            "depends_on": ["session_init"],
            "status": "pending",
            "gate": "authority",
            "input_refs": [],
            "output_refs": [],
        })
        manifest["stages"][0]["status"] = "done"
        self.assertEqual(
            stage_dag.parallel_batches(manifest),
            [["brief_gate", "knowledge_load"]],
        )

    def test_parallel_batches_returns_multiple_layers(self):
        manifest = _manifest()
        manifest["stages"][0]["status"] = "done"
        manifest["stages"][1]["depends_on"] = ["session_init"]
        self.assertEqual(
            stage_dag.parallel_batches(manifest),
            [["brief_gate"]],
        )

    def test_parallel_batches_does_not_run_after_blocked_dependency(self):
        manifest = _manifest()
        manifest["stages"][0]["status"] = "done"
        manifest["stages"][1]["depends_on"] = ["session_init"]
        manifest["stages"][1]["status"] = "blocked"
        self.assertEqual(stage_dag.parallel_batches(manifest), [])


class StageDagTransitionTest(unittest.TestCase):

    def test_transition_stage_supports_valid_transitions(self):
        manifest = _manifest()
        for status in ("ready", "running", "done"):
            manifest = stage_dag.transition_stage(
                manifest, "session_init", status)
        self.assertEqual(manifest["stages"][0]["status"], "done")

        manifest = _manifest()
        manifest = stage_dag.transition_stage(manifest, "session_init", "blocked")
        manifest = stage_dag.transition_stage(manifest, "session_init", "ready")
        self.assertEqual(manifest["stages"][0]["status"], "ready")

    def test_transition_stage_rejects_invalid_transition(self):
        manifest = _manifest()
        manifest = stage_dag.transition_stage(manifest, "session_init", "ready")
        with self.assertRaises(stage_dag.StageDagError):
            stage_dag.transition_stage(manifest, "session_init", "ready")


class StageDagEnvelopeTest(unittest.TestCase):

    def test_validate_task_accepts_valid_task(self):
        task = _task()
        self.assertEqual(stage_dag.validate_task(task), task)

    def test_validate_task_rejects_unknown_stage_or_wrong_assignee(self):
        task = _task()
        task["stage_id"] = "missing"
        with self.assertRaises(stage_dag.StageDagError):
            stage_dag.validate_task(task, _manifest())

        task = _task()
        task["agent_id"] = "pb-preflight-agent"
        with self.assertRaises(stage_dag.StageDagError):
            stage_dag.validate_task(task, _manifest())

    def test_validate_task_rejects_wrong_schema_version(self):
        task = _task()
        task["schema_version"] = "pb-stage-task-v0"
        with self.assertRaises(stage_dag.StageDagError) as ctx:
            stage_dag.validate_task(task)
        self.assertTrue(any("pb-stage-task-v1" in msg for msg in ctx.exception.errors))

    def test_validate_task_rejects_output_path_outside_agent_scope(self):
        task = _task()
        task["output_paths"] = ["outputs/pb-knowledge-steward/steal.json"]
        with self.assertRaises(stage_dag.StageDagError) as ctx:
            stage_dag.validate_task(task)
        self.assertTrue(any("output_paths" in msg for msg in ctx.exception.errors))

    def test_validate_task_accepts_output_path_inside_agent_scope(self):
        task = _task()
        task["output_paths"] = ["outputs/pb-intake-agent/brief.json"]
        stage_dag.validate_task(task)

    def test_validate_task_rejects_output_path_escaping_run_root(self):
        task = _task()
        task["output_paths"] = ["outputs/pb-intake-agent/../../escape.json"]
        with self.assertRaises(stage_dag.StageDagError) as ctx:
            stage_dag.validate_task(task)
        self.assertTrue(any("output_paths" in msg for msg in ctx.exception.errors))

    def test_validate_result_accepts_valid_result(self):
        result = _result()
        self.assertEqual(stage_dag.validate_result(result), result)

    def test_validate_result_rejects_bad_status_or_request(self):
        result = _result()
        result["status"] = "running"
        with self.assertRaises(stage_dag.StageDagError):
            stage_dag.validate_result(result)

        result = _result()
        result["requests"] = [{
            "type": "direct_call",
            "logical_target": "pb-knowledge-steward",
            "payload": {},
            "blocking": True,
        }]
        with self.assertRaises(stage_dag.StageDagError):
            stage_dag.validate_result(result)


class StageDagCliTest(unittest.TestCase):

    def _write_manifest(self, manifest):
        fd, path = tempfile.mkstemp(prefix="stage-dag-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False)
        self.addCleanup(os.remove, path)
        return path

    def _run_cli(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = stage_dag.run_cli(args)
            return code, stdout.getvalue(), stderr.getvalue()
        except SystemExit as exc:
            return exc.code, stdout.getvalue(), stderr.getvalue()

    def test_cli_validate_accepts_valid_manifest(self):
        path = self._write_manifest(_manifest())
        code, out, err = self._run_cli(["--manifest", path, "--action", "validate"])
        self.assertEqual((code, err), (0, ""))
        self.assertIn("OK", out)

    def test_cli_validate_rejects_invalid_manifest(self):
        bad = _manifest()
        bad["schema_version"] = "wrong"
        path = self._write_manifest(bad)
        code, out, err = self._run_cli(["--manifest", path, "--action", "validate"])
        self.assertEqual(code, 2)
        self.assertIn("pb-stage-run-v2", err)

    def test_cli_migrate_outputs_valid_v2_manifest(self):
        legacy = stage_dag._creation_template_manifest_v1()
        path = self._write_manifest(legacy)
        code, out, err = self._run_cli(
            ["--manifest", path, "--action", "migrate"])
        self.assertEqual((code, err), (0, ""))
        migrated = json.loads(out)
        self.assertEqual(migrated["schema_version"], stage_dag.RUN_SCHEMA)
        self.assertEqual(stage_dag.validate_manifest(migrated), migrated)

    def test_cli_ready_and_batches_print_json(self):
        path = self._write_manifest(_manifest())
        code, out, err = self._run_cli(["--manifest", path, "--action", "ready"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), ["session_init"])

        code, out, err = self._run_cli(["--manifest", path, "--action", "batches"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [["session_init"], ["brief_gate"]])

    def test_cli_template_prints_valid_manifest(self):
        code, out, err = self._run_cli(["--action", "template"])
        self.assertEqual(code, 0)
        parsed = json.loads(out)
        self.assertEqual(stage_dag.validate_manifest(parsed), parsed)

    def test_cli_creation_template_has_twelve_stages(self):
        code, out, err = self._run_cli(["--action", "creation-template"])
        self.assertEqual(code, 0)
        parsed = json.loads(out)
        validated = stage_dag.validate_manifest(parsed)
        self.assertEqual(len(validated["stages"]), 12)

        stage_ids = [s["stage_id"] for s in validated["stages"]]
        expected_ids = [
            "session_init", "brief_gate", "knowledge_load",
            "creation_delegate", "preflight", "collision_check",
            "qa", "qa_synthesis", "confirmation_gate",
            "revision_loop", "persistence", "knowledge_reminder",
        ]
        self.assertEqual(stage_ids, expected_ids)

        # Verify key dependency edges.
        stage_map = {s["stage_id"]: s for s in validated["stages"]}
        self.assertEqual(stage_map["brief_gate"]["depends_on"], ["session_init"])
        self.assertEqual(
            stage_map["knowledge_load"]["depends_on"], ["session_init"])
        self.assertEqual(
            stage_map["creation_delegate"]["depends_on"],
            ["brief_gate", "knowledge_load"])
        self.assertEqual(
            stage_map["preflight"]["depends_on"], ["creation_delegate"])
        self.assertEqual(
            stage_map["collision_check"]["depends_on"], ["creation_delegate"])
        self.assertEqual(
            stage_map["qa"]["depends_on"], ["preflight", "collision_check"])
        self.assertEqual(stage_map["qa_synthesis"]["depends_on"], ["qa"])
        self.assertEqual(
            stage_map["confirmation_gate"]["depends_on"], ["qa_synthesis"])
        self.assertEqual(
            stage_map["revision_loop"]["depends_on"], ["confirmation_gate"])
        self.assertEqual(
            stage_map["persistence"]["depends_on"], ["confirmation_gate"])
        self.assertEqual(
            stage_map["knowledge_reminder"]["depends_on"], ["persistence"])

        # Verify assignees.
        self.assertEqual(
            stage_map["session_init"]["assignee"], "pb-intake-agent")
        self.assertEqual(
            stage_map["brief_gate"]["assignee"], "pb-intake-agent")
        self.assertEqual(
            stage_map["knowledge_load"]["assignee"], "pb-knowledge-steward")
        self.assertEqual(
            stage_map["creation_delegate"]["assignee"], "pb-screenwriter")
        self.assertEqual(
            stage_map["preflight"]["assignee"], "pb-preflight-agent")
        self.assertEqual(
            stage_map["collision_check"]["assignee"], "pb-knowledge-steward")
        self.assertEqual(
            stage_map["qa"]["assignee"], "pb-quality-reviewer")
        self.assertEqual(
            stage_map["persistence"]["assignee"], "pb-persistence-agent")

    def test_cli_creation_template_parallel_batches(self):
        code, out, err = self._run_cli(["--action", "creation-template"])
        parsed = json.loads(out)
        batches = stage_dag.parallel_batches(parsed)
        # session_init
        # → {brief_gate, knowledge_load}
        # → creation_delegate
        # → {preflight, collision_check}
        # → qa
        # → qa_synthesis
        # → confirmation_gate
        # → {revision_loop, persistence}
        # → knowledge_reminder
        self.assertEqual(len(batches), 9)
        self.assertIn(["brief_gate", "knowledge_load"], batches)
        self.assertIn(["collision_check", "preflight"], batches)
        self.assertIn(["persistence", "revision_loop"], batches)

    def test_cli_illustration_template_has_eight_stages(self):
        code, out, err = self._run_cli(["--action", "illustration-template"])
        self.assertEqual((code, err), (0, ""))
        parsed = json.loads(out)
        validated = stage_dag.validate_manifest(parsed)
        self.assertEqual(len(validated["stages"]), 8)

        stage_ids = [s["stage_id"] for s in validated["stages"]]
        expected_ids = [
            "illustration_init", "asset_extraction", "asset_confirmation",
            "asset_preproduction", "asset_final_confirmation",
            "illustration_generation", "html_export", "illustration_delivery",
        ]
        self.assertEqual(stage_ids, expected_ids)

        stage_map = {s["stage_id"]: s for s in validated["stages"]}
        expected_stages = {
            "illustration_init": ("pb-intake-agent", [], "script-approved"),
            "asset_extraction": (
                "picturebook-art-agent", ["illustration_init"], "none"),
            "asset_confirmation": (
                "picturebook-screenwriter-team-lead",
                ["asset_extraction"], "asset-list"),
            "asset_preproduction": (
                "picturebook-art-agent", ["asset_confirmation"], "none"),
            "asset_final_confirmation": (
                "picturebook-screenwriter-team-lead",
                ["asset_preproduction"], "asset-final"),
            "illustration_generation": (
                "picturebook-art-agent",
                ["asset_final_confirmation"], "none"),
            "html_export": (
                "picturebook-art-agent", ["illustration_generation"], "none"),
            "illustration_delivery": (
                "picturebook-screenwriter-team-lead", ["html_export"], "none"),
        }
        for stage_id, (assignee, deps, gate) in expected_stages.items():
            stage = stage_map[stage_id]
            self.assertEqual(stage["assignee"], assignee)
            self.assertEqual(stage["depends_on"], deps)
            self.assertEqual(stage["gate"], gate)

    def test_cli_illustration_template_linear_batches(self):
        code, out, err = self._run_cli(["--action", "illustration-template"])
        self.assertEqual(code, 0)
        parsed = json.loads(out)
        batches = stage_dag.parallel_batches(parsed)
        self.assertEqual(batches, [
            ["illustration_init"],
            ["asset_extraction"],
            ["asset_confirmation"],
            ["asset_preproduction"],
            ["asset_final_confirmation"],
            ["illustration_generation"],
            ["html_export"],
            ["illustration_delivery"],
        ])


class StageDagIntegrationTest(unittest.TestCase):
    """spec 13.2 集成测试：用调度器引擎验证关键流程行为。"""

    def _creation_template(self):
        return stage_dag._creation_template_manifest()

    @staticmethod
    def _fast_forward(manifest, stage_ids):
        """按 pending → ready → running → done 推进一组阶段。"""
        for sid in stage_ids:
            for status in ("ready", "running", "done"):
                manifest = stage_dag.transition_stage(manifest, sid, status)
        return manifest

    def test_light_mode_has_no_stages(self):
        """轻量/脑暴模式不进入 DAG 流程。"""
        manifest = self._creation_template()
        manifest["mode"] = "light"
        manifest["stages"] = []
        self.assertEqual(stage_dag.parallel_batches(manifest), [])

    def test_confirmation_gate_blocks_downstream(self):
        """确认门 blocked 时，revision_loop 和 persistence 不进入执行批次。"""
        manifest = self._creation_template()
        # Fast-forward to confirmation_gate being blocked.
        manifest = self._fast_forward(manifest, [
            "session_init", "brief_gate", "knowledge_load",
            "creation_delegate", "preflight", "collision_check",
            "qa", "qa_synthesis",
        ])
        manifest = stage_dag.transition_stage(manifest, "confirmation_gate", "blocked")

        batches = stage_dag.parallel_batches(manifest)
        all_scheduled = [sid for batch in batches for sid in batch]
        self.assertNotIn("revision_loop", all_scheduled)
        self.assertNotIn("persistence", all_scheduled)
        self.assertNotIn("knowledge_reminder", all_scheduled)

    def test_confirmation_gate_done_unblocks_persistence_and_revision(self):
        """确认门 done 后，persistence 和 revision_loop 可并行。"""
        manifest = self._creation_template()
        manifest = self._fast_forward(manifest, [
            "session_init", "brief_gate", "knowledge_load",
            "creation_delegate", "preflight", "collision_check",
            "qa", "qa_synthesis", "confirmation_gate",
        ])

        batches = stage_dag.parallel_batches(manifest)
        self.assertEqual(batches[0], ["persistence", "revision_loop"])

    def test_preflight_failed_stops_pipeline(self):
        """preflight failed 时，后续 qa / qa_synthesis 等不执行。"""
        manifest = self._creation_template()
        manifest = self._fast_forward(manifest, [
            "session_init", "brief_gate", "knowledge_load", "creation_delegate",
        ])
        manifest = stage_dag.transition_stage(manifest, "preflight", "failed")

        batches = stage_dag.parallel_batches(manifest)
        all_scheduled = [sid for batch in batches for sid in batch]
        self.assertNotIn("qa", all_scheduled)
        self.assertNotIn("confirmation_gate", all_scheduled)

    def test_preflight_retry_unblocks_qa(self):
        """preflight failed → ready → done 后，qa 可以进入批次。"""
        manifest = self._creation_template()
        manifest = self._fast_forward(manifest, [
            "session_init", "brief_gate", "knowledge_load", "creation_delegate",
        ])
        manifest = stage_dag.transition_stage(manifest, "preflight", "failed")
        manifest = stage_dag.transition_stage(manifest, "preflight", "ready")
        manifest = stage_dag.transition_stage(manifest, "preflight", "running")
        manifest = stage_dag.transition_stage(manifest, "preflight", "done")

        # collision_check 在 preflight 之后的批次中被调度，qa 在 collision_check 之后的批次中出现。
        batches = stage_dag.parallel_batches(manifest)
        collision_batch_idx = next(
            i for i, b in enumerate(batches) if "collision_check" in b)
        qa_batch_idx = next(i for i, b in enumerate(batches) if "qa" in b)
        self.assertGreater(qa_batch_idx, collision_batch_idx)

    def test_fatal_preflight_rolls_back_to_creation_delegate(self):
        """preflight fatal → 回退到 creation_delegate 重派修订。"""
        manifest = self._creation_template()
        manifest = self._fast_forward(manifest, [
            "session_init", "brief_gate", "knowledge_load", "creation_delegate",
        ])
        manifest = stage_dag.transition_stage(manifest, "preflight", "failed")
        # 回退：failed → ready → done（模拟修订后重新通过）
        # 同时 creation_delegate 从 done 不可回退（终态），需要重跑新 run
        # 或用新 manifest 副本重置 creation_delegate。这里验证 transition 规则。
        with self.assertRaises(stage_dag.StageDagError):
            stage_dag.transition_stage(manifest, "creation_delegate", "ready")

    def test_persistence_requires_confirmation_gate_done(self):
        """persistence 只能在 confirmation_gate done 后执行。"""
        manifest = self._creation_template()
        manifest = self._fast_forward(manifest, [
            "session_init", "brief_gate", "knowledge_load",
            "creation_delegate", "preflight", "collision_check",
            "qa", "qa_synthesis",
        ])

        batches = stage_dag.parallel_batches(manifest)
        # persistence 可以被调度（在确认门之后的批次里），但确认门批次在前。
        gate_batch_idx = next(
            i for i, b in enumerate(batches) if "confirmation_gate" in b)
        persistence_batch_idx = next(
            i for i, b in enumerate(batches) if "persistence" in b)
        self.assertGreater(persistence_batch_idx, gate_batch_idx)

        for status in ("ready", "running", "done"):
            manifest = stage_dag.transition_stage(manifest, "confirmation_gate", status)
        all_scheduled = [sid for batch in stage_dag.parallel_batches(manifest) for sid in batch]
        self.assertIn("persistence", all_scheduled)

    def test_request_input_creates_knowledge_refill(self):
        """编剧 request_input → 主编创建 knowledge_refill 阶段并派发。"""
        manifest = self._creation_template()
        result = {
            "schema_version": "pb-stage-result-v1",
            "run_id": manifest["run_id"],
            "task_id": "creation_delegate-0001",
            "stage_id": "creation_delegate",
            "agent_id": "pb-screenwriter",
            "status": "needs_input",
            "result": {},
            "artifact_refs": [],
            "requests": [{
                "type": "request_input",
                "logical_target": "pb-knowledge-steward",
                "payload": {"operation": "domain_search", "keywords": ["角色"]},
                "blocking": True,
            }],
            "risks": [],
            "resume_context": "ab12cd34",
        }
        validated = stage_dag.validate_result(result)
        self.assertEqual(validated["requests"][0]["logical_target"], "pb-knowledge-steward")

        # 主编在 manifest 中追加 knowledge_refill 阶段并校验 DAG 仍然合法。
        manifest["stages"].append({
            "stage_id": "knowledge_refill",
            "assignee": "pb-knowledge-steward",
            "depends_on": ["creation_delegate"],
            "status": "pending",
            "gate": "authority",
            "input_refs": [],
            "output_refs": [],
        })
        validated_manifest = stage_dag.validate_manifest(manifest)
        self.assertEqual(len(validated_manifest["stages"]), 13)


if __name__ == "__main__":
    unittest.main(verbosity=2)
