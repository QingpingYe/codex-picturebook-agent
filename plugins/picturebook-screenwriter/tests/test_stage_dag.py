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

    def test_validate_manifest_rejects_invalid_when_outcome(self):
        bad = _manifest()
        bad["stages"][1]["when"] = {
            "stage_id": "session_init",
            "outcome": "banana",
        }
        with self.assertRaises(stage_dag.StageDagError) as ctx:
            stage_dag.validate_manifest(bad)
        self.assertTrue(
            any("when.outcome" in msg for msg in ctx.exception.errors))

    def test_validate_manifest_rejects_when_referencing_non_confirmation_stage(self):
        bad = _manifest()
        bad["stages"][1]["when"] = {
            "stage_id": "session_init",
            "outcome": "approved",
        }
        with self.assertRaises(stage_dag.StageDagError) as ctx:
            stage_dag.validate_manifest(bad)
        self.assertTrue(any("confirmation" in msg for msg in ctx.exception.errors))

    def test_validate_manifest_rejects_non_string_when_values(self):
        malformed_when_values = (
            {"stage_id": ["session_init"], "outcome": "approved"},
            {"stage_id": "session_init", "outcome": ["approved"]},
        )

        for when in malformed_when_values:
            with self.subTest(when=when):
                bad = _manifest()
                bad["stages"][1]["when"] = when

                with self.assertRaises(stage_dag.StageDagError):
                    stage_dag.validate_manifest(bad)

    def test_validate_manifest_rejects_done_confirmation_gate_without_outcome(self):
        bad = _manifest()
        bad["stages"][1]["gate"] = "confirmation"
        bad["stages"][1]["status"] = "done"

        with self.assertRaisesRegex(stage_dag.StageDagError, "outcome"):
            stage_dag.validate_manifest(bad)

    def test_validate_manifest_rejects_confirmation_outcome_before_done(self):
        bad = _manifest()
        bad["stages"][1]["gate"] = "confirmation"
        bad["stages"][1]["status"] = "pending"
        bad["stages"][1]["outcome"] = "approved"

        with self.assertRaisesRegex(stage_dag.StageDagError, "done"):
            stage_dag.validate_manifest(bad)

    def test_validate_manifest_rejects_terminal_stage_without_required_reason(self):
        malformed = (
            ("skipped", "skip_reason", "skip_reason"),
            ("blocked", "blocked_reason", "blocked_reason"),
        )
        for status, missing_key, message in malformed:
            with self.subTest(status=status):
                bad = _manifest()
                bad["stages"][0]["status"] = status
                bad["stages"][0][missing_key] = None

                with self.assertRaisesRegex(
                        stage_dag.StageDagError, message):
                    stage_dag.validate_manifest(bad)

    def test_validate_manifest_rejects_incompatible_run_lifecycle(self):
        invalid_overrides = (
            {"mode": "turbo"},
            {"status": "completed", "outcome": None},
            {"status": "cancelled", "outcome": None},
            {"status": "pending", "outcome": "approved"},
        )
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                with self.assertRaises(stage_dag.StageDagError):
                    stage_dag.validate_manifest(_manifest(**overrides))

    def test_validate_manifest_rejects_incomplete_revision_manifest(self):
        invalid_overrides = (
            {
                "intent": "revision",
                "revision_of_run_id": "20260922-example-0000",
                "iteration": 2,
                "revision_feedback": [],
                "source_artifact_ref": "picturebook/script_v1.md",
            },
            {
                "intent": "revision",
                "revision_of_run_id": "20260922-example-0000",
                "iteration": 2,
                "revision_feedback": [{"page": 1}],
                "source_artifact_ref": None,
            },
            {
                "intent": "revision",
                "revision_of_run_id": "20260922-example-0001",
                "iteration": 2,
                "revision_feedback": [{"page": 1}],
                "source_artifact_ref": "picturebook/script_v1.md",
            },
        )
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                with self.assertRaises(stage_dag.StageDagError):
                    stage_dag.validate_manifest(_manifest(**overrides))


class StageDagMigrationTest(unittest.TestCase):

    @staticmethod
    def _as_v1(template):
        legacy = copy.deepcopy(template)
        legacy["schema_version"] = stage_dag.RUN_SCHEMA_V1
        for key in (
            "root_run_id", "revision_of_run_id", "iteration", "status",
            "outcome", "revision_feedback", "source_artifact_ref",
        ):
            legacy.pop(key, None)
        for stage in legacy["stages"]:
            for key in (
                "outcome", "when", "skip_reason", "blocked_reason",
            ):
                stage.pop(key, None)
        return legacy

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

    def test_migrate_v1_supports_generic_template(self):
        legacy = self._as_v1(stage_dag._template_manifest())

        migrated = stage_dag.migrate_manifest_v1(legacy)

        self.assertEqual(migrated["schema_version"], stage_dag.RUN_SCHEMA)
        self.assertEqual(migrated["root_run_id"], legacy["run_id"])
        self.assertEqual(migrated["iteration"], 1)
        self.assertIsNone(migrated["revision_of_run_id"])
        self.assertEqual(
            [stage["stage_id"] for stage in migrated["stages"]],
            ["session_init", "brief_gate"],
        )

    def test_migrate_v1_supports_illustration_template(self):
        legacy = self._as_v1(stage_dag._illustration_template_manifest())

        migrated = stage_dag.migrate_manifest_v1(legacy)

        self.assertEqual(migrated["schema_version"], stage_dag.RUN_SCHEMA)
        self.assertEqual(migrated["artifact_type"], "illustration")
        self.assertEqual(
            [stage["stage_id"] for stage in migrated["stages"]],
            [stage["stage_id"] for stage in legacy["stages"]],
        )

    def test_migrate_preserves_completed_approved_creation_run(self):
        legacy = stage_dag._creation_template_manifest_v1()
        by_id = {
            stage["stage_id"]: stage for stage in legacy["stages"]
        }
        for stage_id in (
            "session_init", "brief_gate", "knowledge_load",
            "creation_delegate", "preflight", "collision_check",
            "qa", "qa_synthesis",
        ):
            by_id[stage_id]["status"] = "done"
        by_id["confirmation_gate"]["status"] = "done"
        by_id["confirmation_gate"]["outcome"] = "approved"
        by_id["persistence"]["status"] = "done"
        by_id["knowledge_reminder"]["status"] = "done"

        migrated = stage_dag.migrate_manifest_v1(legacy)
        stages = {
            stage["stage_id"]: stage for stage in migrated["stages"]
        }

        self.assertEqual(migrated["status"], "completed")
        self.assertEqual(migrated["outcome"], "approved")
        self.assertEqual(stages["confirmation_gate"]["outcome"], "approved")
        self.assertNotIn("revision_loop", stages)

    def test_migrate_preserves_completed_revision_request(self):
        legacy = stage_dag._creation_template_manifest_v1()
        by_id = {
            stage["stage_id"]: stage for stage in legacy["stages"]
        }
        by_id["confirmation_gate"]["status"] = "done"
        by_id["confirmation_gate"]["outcome"] = "revision_requested"
        by_id["revision_loop"]["status"] = "done"

        migrated = stage_dag.migrate_manifest_v1(legacy)
        stages = {
            stage["stage_id"]: stage for stage in migrated["stages"]
        }

        self.assertEqual(migrated["status"], "completed")
        self.assertEqual(migrated["outcome"], "revision_requested")
        self.assertEqual(stages["persistence"]["status"], "skipped")
        self.assertEqual(stages["knowledge_reminder"]["status"], "skipped")
        self.assertEqual(
            stages["persistence"]["skip_reason"],
            "confirmation_requested_revision",
        )

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

    def test_migrate_rejects_non_dict_stage(self):
        legacy = stage_dag._creation_template_manifest_v1()
        legacy["stages"].append("not-a-stage")

        with self.assertRaisesRegex(
                stage_dag.StageDagError, "stages\\[12\\] 必须是对象"):
            stage_dag.migrate_manifest_v1(legacy)

    def test_migrate_rejects_invalid_stage_id_shapes(self):
        for stage_id in (None, ["session_init"], 1):
            with self.subTest(stage_id=stage_id):
                legacy = stage_dag._creation_template_manifest_v1()
                if stage_id is None:
                    legacy["stages"][0].pop("stage_id")
                else:
                    legacy["stages"][0]["stage_id"] = stage_id

                with self.assertRaisesRegex(
                        stage_dag.StageDagError, "stage_id"):
                    stage_dag.migrate_manifest_v1(legacy)

    def test_migrate_rejects_invalid_stage_shapes(self):
        malformed_stages = {
            "non_list_dependencies": {"depends_on": None},
            "non_string_dependency": {"depends_on": [["session_init"]]},
            "invalid_status": {"status": ["pending"]},
            "non_confirmation_outcome": {"outcome": ["approved"]},
        }

        for name, override in malformed_stages.items():
            with self.subTest(name=name):
                legacy = stage_dag._creation_template_manifest_v1()
                legacy["stages"][0].update(override)

                with self.assertRaises(stage_dag.StageDagError):
                    stage_dag.migrate_manifest_v1(legacy)

    def test_migrate_rejects_missing_required_top_level_fields(self):
        for key in ("run_id", "intent", "artifact_type", "mode", "project_root"):
            with self.subTest(key=key):
                legacy = stage_dag._creation_template_manifest_v1()
                legacy.pop(key)

                with self.assertRaisesRegex(
                        stage_dag.StageDagError, key):
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
        manifest["stages"][1]["blocked_reason"] = "dependency failed"
        self.assertEqual(stage_dag.parallel_batches(manifest), [])


class StageDagConditionalTest(unittest.TestCase):

    @staticmethod
    def _complete_confirmation(manifest, outcome):
        manifest = stage_dag.transition_stage(
            manifest,
            "confirmation_gate",
            "ready",
        )
        manifest = stage_dag.transition_stage(
            manifest,
            "confirmation_gate",
            "running",
        )
        return stage_dag.transition_stage(
            manifest,
            "confirmation_gate",
            "done",
            outcome=outcome,
        )

    @staticmethod
    def _conditional_manifest():
        return _manifest(stages=[
            {
                "stage_id": "confirmation_gate",
                "assignee": "picturebook-screenwriter-team-lead",
                "depends_on": [],
                "status": "pending",
                "gate": "confirmation",
                "outcome": None,
                "when": None,
                "input_refs": [],
                "output_refs": [],
                "skip_reason": None,
                "blocked_reason": None,
            },
            {
                "stage_id": "persistence",
                "assignee": "pb-persistence-agent",
                "depends_on": ["confirmation_gate"],
                "status": "pending",
                "gate": "none",
                "outcome": None,
                "when": {
                    "stage_id": "confirmation_gate",
                    "outcome": "approved",
                },
                "input_refs": [],
                "output_refs": [],
                "skip_reason": None,
                "blocked_reason": None,
            },
            {
                "stage_id": "knowledge_reminder",
                "assignee": "picturebook-screenwriter-team-lead",
                "depends_on": ["persistence"],
                "status": "pending",
                "gate": "none",
                "outcome": None,
                "when": None,
                "input_refs": [],
                "output_refs": [],
                "skip_reason": None,
                "blocked_reason": None,
            },
        ])

    def test_persistence_is_skipped_when_confirmation_is_revision_requested(self):
        manifest = self._conditional_manifest()
        manifest = self._complete_confirmation(manifest, "revision_requested")

        decisions = {
            item.stage_id: item
            for item in stage_dag.resolve_stage_decisions(manifest)
        }
        self.assertEqual(decisions["persistence"].decision, "skip")

    def test_persistence_is_ready_only_after_approved(self):
        manifest = self._conditional_manifest()
        manifest = self._complete_confirmation(manifest, "approved")
        self.assertEqual(stage_dag.ready_stages(manifest), ["persistence"])

    def test_persistence_is_not_ready_after_revision_requested(self):
        manifest = self._conditional_manifest()
        manifest = self._complete_confirmation(manifest, "revision_requested")
        self.assertEqual(stage_dag.ready_stages(manifest), [])

    def test_matched_condition_is_ready_when_other_dependency_is_skipped(self):
        manifest = self._conditional_manifest()
        manifest = self._complete_confirmation(manifest, "approved")
        manifest["stages"].insert(1, {
            "stage_id": "optional_dependency",
            "assignee": "pb-knowledge-steward",
            "depends_on": [],
            "status": "pending",
            "gate": "none",
            "outcome": None,
            "when": None,
            "input_refs": [],
            "output_refs": [],
            "skip_reason": None,
            "blocked_reason": None,
        })
        persistence = next(
            stage for stage in manifest["stages"]
            if stage["stage_id"] == "persistence"
        )
        persistence["depends_on"] = [
            "confirmation_gate",
            "optional_dependency",
        ]
        manifest = stage_dag.transition_stage(
            manifest,
            "optional_dependency",
            "skipped",
            skip_reason="not required",
        )

        decisions = {
            item.stage_id: item
            for item in stage_dag.resolve_stage_decisions(manifest)
        }
        self.assertEqual(decisions["persistence"].decision, "ready")

    def test_next_batches_returns_ready_conditional_stage(self):
        manifest = self._conditional_manifest()
        manifest = self._complete_confirmation(manifest, "approved")
        self.assertEqual(stage_dag.next_batches(manifest), [["persistence"]])

    def test_parallel_batches_rejects_conditional_manifest(self):
        manifest = self._conditional_manifest()
        with self.assertRaisesRegex(stage_dag.StageDagError, "next_batches"):
            stage_dag.parallel_batches(manifest)

    def test_finalize_approved_requires_downstream_done(self):
        manifest = self._conditional_manifest()
        manifest = self._complete_confirmation(manifest, "approved")
        for stage_id in ("persistence", "knowledge_reminder"):
            for status in ("ready", "running", "done"):
                manifest = stage_dag.transition_stage(
                    manifest, stage_id, status)

        manifest = stage_dag.finalize_run(manifest, "approved")
        stages = {stage["stage_id"]: stage for stage in manifest["stages"]}
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["outcome"], "approved")
        self.assertEqual(stages["persistence"]["status"], "done")
        self.assertEqual(stages["knowledge_reminder"]["status"], "done")

    def test_finalize_cancelled_marks_run_cancelled(self):
        manifest = self._conditional_manifest()
        manifest = self._complete_confirmation(manifest, "cancelled")
        manifest = stage_dag.finalize_run(manifest, "cancelled")
        stages = {stage["stage_id"]: stage for stage in manifest["stages"]}

        self.assertEqual(manifest["status"], "cancelled")
        self.assertEqual(manifest["outcome"], "cancelled")
        self.assertEqual(stages["persistence"]["status"], "pending")

    def test_finalize_rejects_untouched_confirmation_gate(self):
        manifest = self._conditional_manifest()

        with self.assertRaises(stage_dag.StageDagError):
            stage_dag.finalize_run(manifest, "revision_requested")

    def test_finalize_requires_confirmation_outcome_to_match(self):
        manifest = self._conditional_manifest()
        manifest = self._complete_confirmation(manifest, "approved")

        with self.assertRaisesRegex(stage_dag.StageDagError, "outcome"):
            stage_dag.finalize_run(manifest, "revision_requested")

    def test_terminal_runs_have_no_decisions_or_batches(self):
        manifests = []

        cancelled = self._conditional_manifest()
        cancelled = self._complete_confirmation(cancelled, "cancelled")
        manifests.append(stage_dag.finalize_run(cancelled, "cancelled"))

        failed = self._conditional_manifest()
        failed["status"] = "failed"
        manifests.append(failed)

        completed = self._conditional_manifest()
        completed = self._complete_confirmation(completed, "approved")
        for stage_id in ("persistence", "knowledge_reminder"):
            for status in ("ready", "running", "done"):
                completed = stage_dag.transition_stage(
                    completed, stage_id, status)
        manifests.append(stage_dag.finalize_run(completed, "approved"))

        for manifest in manifests:
            with self.subTest(status=manifest["status"]):
                self.assertEqual(stage_dag.ready_stages(manifest), [])
                self.assertEqual(
                    stage_dag.resolve_stage_decisions(manifest), [])
                self.assertEqual(stage_dag.next_batches(manifest), [])
                self.assertEqual(stage_dag.parallel_batches(manifest), [])

    def test_finalize_revision_request_skips_downstream(self):
        manifest = self._conditional_manifest()
        manifest = self._complete_confirmation(manifest, "revision_requested")
        manifest = stage_dag.finalize_run(manifest, "revision_requested")
        stages = {stage["stage_id"]: stage for stage in manifest["stages"]}

        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["outcome"], "revision_requested")
        self.assertEqual(stages["persistence"]["status"], "skipped")
        self.assertEqual(stages["knowledge_reminder"]["status"], "skipped")


class StageDagRevisionTest(unittest.TestCase):

    def setUp(self):
        self._project_dir = tempfile.TemporaryDirectory()
        self.project_root = self._project_dir.name
        self.artifact_ref = "picturebook/script_v1.md"
        artifact_path = os.path.join(
            self.project_root, "picturebook", "script_v1.md")
        os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
        with open(artifact_path, "w", encoding="utf-8") as fh:
            fh.write("approved draft\n")

    def tearDown(self):
        self._project_dir.cleanup()

    def _revision_parent(self):
        manifest = stage_dag._creation_template_manifest()
        manifest["project_root"] = self.project_root
        for stage_id in (
            "session_init", "brief_gate", "knowledge_load",
            "creation_delegate", "preflight", "collision_check",
            "qa", "qa_synthesis",
        ):
            for status in ("ready", "running", "done"):
                manifest = stage_dag.transition_stage(
                    manifest, stage_id, status)
        for status in ("ready", "running"):
            manifest = stage_dag.transition_stage(
                manifest, "confirmation_gate", status)
        manifest = stage_dag.transition_stage(
            manifest,
            "confirmation_gate",
            "done",
            outcome="revision_requested",
        )
        return stage_dag.finalize_run(manifest, "revision_requested")

    def test_creation_template_has_no_revision_loop(self):
        manifest = stage_dag._creation_template_manifest()
        stage_ids = [stage["stage_id"] for stage in manifest["stages"]]
        self.assertNotIn("revision_loop", stage_ids)
        persistence = next(
            stage for stage in manifest["stages"]
            if stage["stage_id"] == "persistence"
        )
        self.assertEqual(
            persistence["when"],
            {"stage_id": "confirmation_gate", "outcome": "approved"},
        )

    def test_build_revision_manifest_increments_iteration_and_preserves_root(self):
        parent = self._revision_parent()
        revision = stage_dag.build_revision_manifest(
            parent,
            feedback=[{
                "page": 5,
                "issue": "钩子偏弱",
                "instruction": "加强悬念",
            }],
            artifact_ref=self.artifact_ref,
            run_id="20260922-example-0002",
        )
        self.assertEqual(revision["intent"], "revision")
        self.assertEqual(revision["iteration"], 2)
        self.assertEqual(revision["root_run_id"], parent["root_run_id"])
        self.assertEqual(
            revision["revision_of_run_id"], parent["run_id"])
        self.assertEqual(revision["artifact_type"], parent["artifact_type"])
        self.assertEqual(revision["project_root"], parent["project_root"])
        self.assertEqual(
            revision["revision_feedback"],
            [{
                "page": 5,
                "issue": "钩子偏弱",
                "instruction": "加强悬念",
            }],
        )
        self.assertEqual(
            revision["source_artifact_ref"], self.artifact_ref)

    def test_build_revision_manifest_rejects_parent_run_id_reuse(self):
        parent = self._revision_parent()

        with self.assertRaisesRegex(stage_dag.StageDagError, "run_id"):
            stage_dag.build_revision_manifest(
                parent,
                feedback=[{"page": 1, "issue": "问题", "instruction": "修改"}],
                artifact_ref=self.artifact_ref,
                run_id=parent["run_id"],
            )

    def test_build_revision_manifest_rejects_unreadable_source_artifact(self):
        parent = self._revision_parent()
        artifact_refs = (
            "picturebook/missing.md",
            "../outside-project.md",
            os.path.join(self.project_root, "picturebook", "script_v1.md"),
        )

        for artifact_ref in artifact_refs:
            with self.subTest(artifact_ref=artifact_ref):
                with self.assertRaises(stage_dag.StageDagError):
                    stage_dag.build_revision_manifest(
                        parent,
                        feedback=[{
                            "page": 1,
                            "issue": "问题",
                            "instruction": "修改",
                        }],
                        artifact_ref=artifact_ref,
                        run_id="20260922-example-0002",
                    )

    def test_revision_template_reruns_checks_and_stops_at_new_gate(self):
        revision = stage_dag.build_revision_manifest(
            self._revision_parent(),
            feedback=[{
                "page": 5,
                "issue": "钩子偏弱",
                "instruction": "加强悬念",
            }],
            artifact_ref=self.artifact_ref,
            run_id="20260922-example-0002",
        )
        self.assertEqual(
            [stage["stage_id"] for stage in revision["stages"]],
            [
                "revision_init",
                "knowledge_load",
                "revision_delegate",
                "preflight",
                "collision_check",
                "qa",
                "qa_synthesis",
                "confirmation_gate",
                "persistence",
                "knowledge_reminder",
            ],
        )
        stages = {
            stage["stage_id"]: stage for stage in revision["stages"]
        }
        self.assertEqual(
            stages["knowledge_load"]["depends_on"], ["revision_init"])
        self.assertEqual(
            stages["revision_delegate"]["depends_on"],
            ["revision_init", "knowledge_load"],
        )
        self.assertEqual(
            stages["preflight"]["depends_on"], ["revision_delegate"])
        self.assertEqual(
            stages["collision_check"]["depends_on"], ["revision_delegate"])
        self.assertEqual(
            stages["qa"]["depends_on"],
            ["preflight", "collision_check"],
        )
        self.assertEqual(
            stages["persistence"]["when"],
            {"stage_id": "confirmation_gate", "outcome": "approved"},
        )

        for stage_id in (
            "revision_init", "knowledge_load", "revision_delegate",
            "preflight", "collision_check", "qa", "qa_synthesis",
        ):
            for status in ("ready", "running", "done"):
                revision = stage_dag.transition_stage(
                    revision, stage_id, status)
        self.assertEqual(
            stage_dag.next_batches(revision), [["confirmation_gate"]])

    def test_build_revision_manifest_rejects_parent_not_completed(self):
        parent = stage_dag._creation_template_manifest()
        parent["outcome"] = "revision_requested"

        with self.assertRaisesRegex(stage_dag.StageDagError, "completed"):
            stage_dag.build_revision_manifest(
                parent,
                feedback=[{"page": 1, "issue": "问题", "instruction": "修改"}],
                artifact_ref=self.artifact_ref,
                run_id="20260922-example-0002",
            )

    def test_build_revision_manifest_rejects_parent_without_revision_outcome(self):
        parent = self._revision_parent()
        parent["outcome"] = "approved"

        with self.assertRaisesRegex(
                stage_dag.StageDagError, "revision_requested"):
            stage_dag.build_revision_manifest(
                parent,
                feedback=[{"page": 1, "issue": "问题", "instruction": "修改"}],
                artifact_ref=self.artifact_ref,
                run_id="20260922-example-0002",
            )

    def test_build_revision_manifest_rejects_empty_feedback(self):
        for feedback in ([], None, "not-a-list"):
            with self.subTest(feedback=feedback):
                with self.assertRaisesRegex(
                        stage_dag.StageDagError, "revision_feedback"):
                    stage_dag.build_revision_manifest(
                        self._revision_parent(),
                        feedback=feedback,
                        artifact_ref=self.artifact_ref,
                        run_id="20260922-example-0002",
                    )

    def test_build_revision_manifest_rejects_missing_artifact_ref(self):
        for artifact_ref in (None, "", "   "):
            with self.subTest(artifact_ref=artifact_ref):
                with self.assertRaisesRegex(
                        stage_dag.StageDagError, "artifact_ref"):
                    stage_dag.build_revision_manifest(
                        self._revision_parent(),
                        feedback=[{
                            "page": 1,
                            "issue": "问题",
                            "instruction": "修改",
                        }],
                        artifact_ref=artifact_ref,
                        run_id="20260922-example-0002",
                    )


class StageDagDecisionTest(unittest.TestCase):

    def test_resolve_stage_decisions_blocks_on_failed_dependency(self):
        manifest = _manifest()
        manifest = stage_dag.transition_stage(
            manifest, "session_init", "failed")
        decisions = {
            item.stage_id: item
            for item in stage_dag.resolve_stage_decisions(manifest)
        }
        self.assertEqual(decisions["brief_gate"].decision, "block")

    def test_resolve_stage_decisions_skips_after_skipped_dependency(self):
        manifest = _manifest()
        manifest = stage_dag.transition_stage(
            manifest,
            "session_init",
            "skipped",
            skip_reason="not required",
        )
        decisions = {
            item.stage_id: item
            for item in stage_dag.resolve_stage_decisions(manifest)
        }
        self.assertEqual(decisions["brief_gate"].decision, "skip")


class StageDagTransitionTest(unittest.TestCase):

    def test_transition_stage_supports_valid_transitions(self):
        manifest = _manifest()
        for status in ("ready", "running", "done"):
            manifest = stage_dag.transition_stage(
                manifest, "session_init", status)
        self.assertEqual(manifest["stages"][0]["status"], "done")

        manifest = _manifest()
        manifest = stage_dag.transition_stage(
            manifest,
            "session_init",
            "blocked",
            blocked_reason="test block",
        )
        manifest = stage_dag.transition_stage(manifest, "session_init", "ready")
        self.assertEqual(manifest["stages"][0]["status"], "ready")

    def test_transition_stage_requires_reasons_for_blocked_and_skipped(self):
        cases = (
            ("blocked", "blocked_reason"),
            ("skipped", "skip_reason"),
        )
        for status, reason_field in cases:
            with self.subTest(status=status):
                with self.assertRaisesRegex(
                        stage_dag.StageDagError, reason_field):
                    stage_dag.transition_stage(
                        _manifest(), "session_init", status)

    def test_transition_requires_valid_confirmation_outcome(self):
        manifest = self._confirmation_manifest()
        for status in ("ready", "running"):
            manifest = stage_dag.transition_stage(
                manifest, "confirmation_gate", status)

        with self.assertRaisesRegex(stage_dag.StageDagError, "outcome"):
            stage_dag.transition_stage(
                manifest, "confirmation_gate", "done")

    def test_transition_rejects_outcome_for_non_confirmation_stage(self):
        manifest = _manifest()
        for status in ("ready", "running"):
            manifest = stage_dag.transition_stage(
                manifest, "session_init", status)

        with self.assertRaisesRegex(stage_dag.StageDagError, "outcome"):
            stage_dag.transition_stage(
                manifest, "session_init", "done", outcome="approved")

    def test_transition_stage_rejects_invalid_transition(self):
        manifest = _manifest()
        manifest = stage_dag.transition_stage(manifest, "session_init", "ready")
        with self.assertRaises(stage_dag.StageDagError):
            stage_dag.transition_stage(manifest, "session_init", "ready")

    @staticmethod
    def _confirmation_manifest():
        manifest = _manifest()
        manifest["stages"][1].update(
            stage_id="confirmation_gate",
            depends_on=[],
            gate="confirmation",
        )
        return manifest


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
        self.assertEqual(json.loads(out), [["session_init"]])

    def test_cli_template_prints_valid_manifest(self):
        code, out, err = self._run_cli(["--action", "template"])
        self.assertEqual(code, 0)
        parsed = json.loads(out)
        self.assertEqual(stage_dag.validate_manifest(parsed), parsed)

    def test_cli_creation_template_has_eleven_stages(self):
        code, out, err = self._run_cli(["--action", "creation-template"])
        self.assertEqual(code, 0)
        parsed = json.loads(out)
        validated = stage_dag.validate_manifest(parsed)
        self.assertEqual(len(validated["stages"]), 11)

        stage_ids = [s["stage_id"] for s in validated["stages"]]
        expected_ids = [
            "session_init", "brief_gate", "knowledge_load",
            "creation_delegate", "preflight", "collision_check",
            "qa", "qa_synthesis", "confirmation_gate",
            "persistence", "knowledge_reminder",
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
            stage_map["persistence"]["depends_on"], ["confirmation_gate"])
        self.assertEqual(
            stage_map["persistence"]["when"],
            {"stage_id": "confirmation_gate", "outcome": "approved"},
        )
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

    def test_cli_creation_template_topological_order(self):
        code, out, err = self._run_cli(["--action", "creation-template"])
        parsed = json.loads(out)
        self.assertEqual(stage_dag.topological_order(parsed), [
            "session_init",
            "brief_gate",
            "knowledge_load",
            "creation_delegate",
            "preflight",
            "collision_check",
            "qa",
            "qa_synthesis",
            "confirmation_gate",
            "persistence",
            "knowledge_reminder",
        ])

    def test_cli_revision_template_prints_valid_manifest(self):
        code, out, err = self._run_cli(["--action", "revision-template"])
        self.assertEqual((code, err), (0, ""))
        parsed = json.loads(out)
        validated = stage_dag.validate_manifest(parsed)
        self.assertEqual(validated["intent"], "revision")
        self.assertEqual(
            [stage["stage_id"] for stage in validated["stages"]],
            [
                "revision_init",
                "knowledge_load",
                "revision_delegate",
                "preflight",
                "collision_check",
                "qa",
                "qa_synthesis",
                "confirmation_gate",
                "persistence",
                "knowledge_reminder",
            ],
        )

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

    def setUp(self):
        self._project_dir = tempfile.TemporaryDirectory()
        self.project_root = self._project_dir.name
        for filename in ("script_v1.md", "script_v2.md"):
            path = os.path.join(
                self.project_root, "picturebook", filename)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("draft\n")

    def tearDown(self):
        self._project_dir.cleanup()

    def _creation_template(self):
        manifest = stage_dag._creation_template_manifest()
        manifest["project_root"] = self.project_root
        return manifest

    @staticmethod
    def _complete_confirmation(manifest, outcome):
        for status in ("ready", "running"):
            manifest = stage_dag.transition_stage(
                manifest,
                "confirmation_gate",
                status,
            )
        return stage_dag.transition_stage(
            manifest,
            "confirmation_gate",
            "done",
            outcome=outcome,
        )

    @staticmethod
    def _fast_forward(manifest, stage_ids):
        """按 pending → ready → running → done 推进一组阶段。"""
        for sid in stage_ids:
            for status in ("ready", "running", "done"):
                manifest = stage_dag.transition_stage(manifest, sid, status)
        return manifest

    def _advance_revision_to_gate(self, manifest):
        return self._fast_forward(manifest, [
            "revision_init",
            "knowledge_load",
            "revision_delegate",
            "preflight",
            "collision_check",
            "qa",
            "qa_synthesis",
        ])

    def test_creation_revision_round_reruns_required_checks(self):
        creation = self._creation_template()
        creation = self._fast_forward(creation, [
            "session_init",
            "brief_gate",
            "knowledge_load",
            "creation_delegate",
            "preflight",
            "collision_check",
            "qa",
            "qa_synthesis",
        ])
        creation = self._complete_confirmation(
            creation,
            "revision_requested",
        )
        creation = stage_dag.finalize_run(
            creation,
            "revision_requested",
        )

        revision = stage_dag.build_revision_manifest(
            creation,
            feedback=[{"page": 5, "issue": "钩子弱", "instruction": "加强"}],
            artifact_ref="picturebook/script_v1.md",
            run_id="20260922-example-0002",
        )

        for expected_batch in (
            ["revision_init"],
            ["knowledge_load"],
            ["revision_delegate"],
            ["collision_check", "preflight"],
            ["qa"],
            ["qa_synthesis"],
        ):
            self.assertEqual(stage_dag.next_batches(revision), [expected_batch])
            revision = self._fast_forward(revision, expected_batch)

        self.assertEqual(
            stage_dag.next_batches(revision),
            [["confirmation_gate"]],
        )
        scheduled = [
            stage_id
            for batch in stage_dag.next_batches(revision)
            for stage_id in batch
        ]
        self.assertNotIn("persistence", scheduled)
        self.assertNotIn("knowledge_reminder", scheduled)
        self.assertEqual(revision["iteration"], 2)

    def test_repeated_revision_preserves_distinct_run_ids(self):
        parent = self._creation_template()
        parent = self._fast_forward(parent, [
            "session_init",
            "brief_gate",
            "knowledge_load",
            "creation_delegate",
            "preflight",
            "collision_check",
            "qa",
            "qa_synthesis",
        ])
        parent = self._complete_confirmation(
            parent,
            "revision_requested",
        )
        parent = stage_dag.finalize_run(parent, "revision_requested")
        first = stage_dag.build_revision_manifest(
            parent,
            feedback=[{"page": 1, "issue": "问题", "instruction": "修改"}],
            artifact_ref="picturebook/script_v1.md",
            run_id="20260922-example-0002",
        )
        first = self._advance_revision_to_gate(first)
        first = self._complete_confirmation(
            first,
            "revision_requested",
        )
        first = stage_dag.finalize_run(first, "revision_requested")
        second = stage_dag.build_revision_manifest(
            first,
            feedback=[{"page": 2, "issue": "问题", "instruction": "再修改"}],
            artifact_ref="picturebook/script_v2.md",
            run_id="20260922-example-0003",
        )

        self.assertEqual(first["run_id"], "20260922-example-0002")
        self.assertEqual(first["root_run_id"], parent["root_run_id"])
        self.assertEqual(first["revision_of_run_id"], parent["run_id"])
        self.assertEqual(first["iteration"], 2)
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(second["root_run_id"], first["root_run_id"])
        self.assertEqual(second["revision_of_run_id"], first["run_id"])
        self.assertEqual(second["iteration"], 3)
        self.assertEqual(stage_dag.validate_manifest(second), second)

    def test_light_mode_has_no_stages(self):
        """轻量/脑暴模式不进入 DAG 流程。"""
        manifest = self._creation_template()
        manifest["mode"] = "light"
        manifest["stages"] = []
        self.assertEqual(stage_dag.parallel_batches(manifest), [])

    def test_confirmation_gate_blocks_downstream(self):
        """确认门 blocked 时，persistence 不进入执行批次。"""
        manifest = self._creation_template()
        # Fast-forward to confirmation_gate being blocked.
        manifest = self._fast_forward(manifest, [
            "session_init", "brief_gate", "knowledge_load",
            "creation_delegate", "preflight", "collision_check",
            "qa", "qa_synthesis",
        ])
        manifest = stage_dag.transition_stage(
            manifest,
            "confirmation_gate",
            "blocked",
            blocked_reason="awaiting confirmation",
        )

        all_scheduled = [
            sid for batch in stage_dag.next_batches(manifest) for sid in batch
        ]
        self.assertNotIn("persistence", all_scheduled)
        self.assertNotIn("knowledge_reminder", all_scheduled)

    def test_confirmation_gate_done_unblocks_persistence_only(self):
        """确认门 approved 后，只放行 persistence。"""
        manifest = self._creation_template()
        manifest = self._fast_forward(manifest, [
            "session_init", "brief_gate", "knowledge_load",
            "creation_delegate", "preflight", "collision_check",
            "qa", "qa_synthesis",
        ])
        for status in ("ready", "running"):
            manifest = stage_dag.transition_stage(
                manifest, "confirmation_gate", status)
        manifest = stage_dag.transition_stage(
            manifest,
            "confirmation_gate",
            "done",
            outcome="approved",
        )

        self.assertEqual(stage_dag.next_batches(manifest), [["persistence"]])

    def test_preflight_failed_stops_pipeline(self):
        """preflight failed 时，后续 qa / qa_synthesis 等不执行。"""
        manifest = self._creation_template()
        manifest = self._fast_forward(manifest, [
            "session_init", "brief_gate", "knowledge_load", "creation_delegate",
        ])
        manifest = stage_dag.transition_stage(manifest, "preflight", "failed")

        all_scheduled = [
            sid for batch in stage_dag.next_batches(manifest) for sid in batch
        ]
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

        self.assertEqual(stage_dag.next_batches(manifest), [["collision_check"]])
        manifest = self._fast_forward(manifest, ["collision_check"])
        self.assertEqual(stage_dag.next_batches(manifest), [["qa"]])

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

        self.assertEqual(
            stage_dag.next_batches(manifest), [["confirmation_gate"]])

        for status in ("ready", "running"):
            manifest = stage_dag.transition_stage(manifest, "confirmation_gate", status)
        manifest = stage_dag.transition_stage(
            manifest,
            "confirmation_gate",
            "done",
            outcome="approved",
        )
        self.assertEqual(stage_dag.next_batches(manifest), [["persistence"]])

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
        self.assertEqual(len(validated_manifest["stages"]), 12)


if __name__ == "__main__":
    unittest.main(verbosity=2)
