#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stage_dag_codex.py 的离线单测（unittest，无网络）。"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import stage_dag
import stage_dag_codex


class StageDagCodexAdapterTest(unittest.TestCase):

    def _fast_forward_without_gate(self, manifest):
        for stage_id in (
            "session_init",
            "brief_gate",
            "knowledge_load",
            "creation_delegate",
            "preflight",
            "collision_check",
            "qa",
            "qa_synthesis",
        ):
            for status in ("ready", "running", "done"):
                manifest = stage_dag.transition_stage(
                    manifest,
                    stage_id,
                    status,
                )
        return manifest

    def _complete_confirmation(self, manifest, outcome):
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

    def _write_manifest(self, manifest):
        fd, path = tempfile.mkstemp(prefix="stage-dag-codex-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False)
        self.addCleanup(os.remove, path)
        return path

    def _run_cli(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = stage_dag_codex.run_cli(args)
            return code, stdout.getvalue(), stderr.getvalue()
        except SystemExit as exc:
            return exc.code, stdout.getvalue(), stderr.getvalue()

    def test_load_manifest_accepts_creation_template(self):
        path = self._write_manifest(stage_dag._creation_template_manifest())
        manifest = stage_dag_codex.load_manifest(path)
        self.assertEqual(manifest["run_id"], "00000000-creation-0001")
        self.assertEqual(len(manifest["stages"]), 11)

    def test_build_dispatch_plan_returns_current_batch_only(self):
        manifest = stage_dag._creation_template_manifest()
        plan = stage_dag_codex.build_dispatch_plan(manifest)

        self.assertEqual(plan["schema_version"], "pb-dispatch-plan-v2")
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(len(plan["next_batches"]), 1)
        self.assertEqual(plan["next_batches"][0]["batch_index"], 0)
        self.assertEqual(
            [
                item["stage_id"]
                for item in plan["next_batches"][0]["stages"]
            ],
            ["session_init"],
        )
        self.assertEqual(
            plan["deferred_stages"],
            [{
                "stage_id": "persistence",
                "reason": "awaiting confirmation_gate outcome",
            }],
        )

    def test_format_task_envelope_validates(self):
        manifest = stage_dag._creation_template_manifest()
        stage = manifest["stages"][1]
        task = stage_dag_codex.format_task_envelope(manifest, stage)

        self.assertEqual(stage_dag.validate_task(task, manifest), task)
        self.assertEqual(task["schema_version"], "pb-stage-task-v1")
        self.assertEqual(task["run_id"], manifest["run_id"])
        self.assertEqual(task["stage_id"], "brief_gate")
        self.assertEqual(task["agent_id"], stage["assignee"])
        self.assertEqual(
            task["inputs"]["run_context"],
            {
                "root_run_id": manifest["root_run_id"],
                "revision_of_run_id": manifest["revision_of_run_id"],
                "iteration": manifest["iteration"],
                "revision_feedback": manifest["revision_feedback"],
                "source_artifact_ref": manifest["source_artifact_ref"],
            },
        )

    def test_build_dispatch_plan_sequential_mode(self):
        manifest = stage_dag._creation_template_manifest()
        manifest["mode"] = "light"
        manifest["stages"] = []
        plan = stage_dag_codex.build_dispatch_plan(manifest)

        self.assertEqual(plan["schema_version"], "pb-dispatch-plan-v2")
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["lead_actions"], [])
        self.assertEqual(plan["next_batches"], [])

    def test_confirmation_gate_is_lead_owned_and_waits_for_user(self):
        manifest = stage_dag._creation_template_manifest()
        manifest = self._fast_forward_without_gate(manifest)
        plan = stage_dag_codex.build_dispatch_plan(manifest)

        self.assertEqual(plan["status"], "waiting_for_user")
        self.assertEqual(
            plan["decision_required"]["stage_id"],
            "confirmation_gate",
        )
        self.assertEqual(plan["next_batches"], [])

    def test_nonlead_confirmation_gate_waits_and_is_not_dispatched(self):
        manifest = stage_dag._creation_template_manifest()
        manifest = self._fast_forward_without_gate(manifest)
        confirmation = next(
            stage
            for stage in manifest["stages"]
            if stage["stage_id"] == "confirmation_gate"
        )
        confirmation["assignee"] = "pb-screenwriter"

        plan = stage_dag_codex.build_dispatch_plan(manifest)

        self.assertEqual(plan["status"], "waiting_for_user")
        self.assertEqual(
            plan["decision_required"]["stage_id"],
            "confirmation_gate",
        )
        self.assertEqual(
            plan["decision_required"]["owner"],
            "picturebook-screenwriter-team-lead",
        )
        self.assertEqual(plan["next_batches"], [])

    def test_misassigned_asset_gates_wait_and_are_not_dispatched(self):
        gate_prerequisites = {
            "asset_confirmation": (
                "illustration_init",
                "asset_extraction",
            ),
            "asset_final_confirmation": (
                "illustration_init",
                "asset_extraction",
                "asset_confirmation",
                "asset_preproduction",
            ),
        }

        for gate_id, prerequisites in gate_prerequisites.items():
            with self.subTest(gate_id=gate_id):
                manifest = stage_dag._illustration_template_manifest()
                for stage_id in prerequisites:
                    for status in ("ready", "running", "done"):
                        manifest = stage_dag.transition_stage(
                            manifest,
                            stage_id,
                            status,
                        )
                gate = next(
                    stage
                    for stage in manifest["stages"]
                    if stage["stage_id"] == gate_id
                )
                gate["assignee"] = "pb-screenwriter"

                plan = stage_dag_codex.build_dispatch_plan(manifest)

                self.assertEqual(plan["status"], "waiting_for_user")
                self.assertEqual(
                    plan["decision_required"]["stage_id"],
                    gate_id,
                )
                self.assertEqual(plan["next_batches"], [])

    def test_approved_confirmation_plans_persistence_only(self):
        manifest = stage_dag._creation_template_manifest()
        manifest = self._fast_forward_without_gate(manifest)
        manifest = self._complete_confirmation(manifest, "approved")
        plan = stage_dag_codex.build_dispatch_plan(manifest)

        self.assertEqual(
            [item["stage_id"] for item in plan["next_batches"][0]["stages"]],
            ["persistence"],
        )

    def test_revision_request_returns_follow_up_action(self):
        manifest = stage_dag._creation_template_manifest()
        manifest = self._fast_forward_without_gate(manifest)
        manifest = self._complete_confirmation(manifest, "revision_requested")
        manifest = stage_dag.finalize_run(manifest, "revision_requested")
        plan = stage_dag_codex.build_dispatch_plan(manifest)

        self.assertEqual(plan["status"], "run_completed")
        self.assertEqual(plan["run_outcome"], "revision_requested")
        self.assertEqual(
            plan["follow_up_action"]["action"],
            "create_revision_run",
        )

    def test_fallback_waits_for_lead_owned_confirmation(self):
        manifest = stage_dag._creation_template_manifest()
        manifest = self._fast_forward_without_gate(manifest)
        plan = stage_dag_codex.build_sequential_fallback(manifest)

        self.assertEqual(plan["schema_version"], "pb-dispatch-plan-v2")
        self.assertEqual(plan["mode"], "sequential")
        self.assertEqual(plan["status"], "waiting_for_user")
        self.assertEqual(
            plan["decision_required"]["stage_id"],
            "confirmation_gate",
        )
        self.assertEqual(plan["next_batches"], [])

    def test_cli_plan_outputs_json(self):
        path = self._write_manifest(stage_dag._creation_template_manifest())
        code, out, err = self._run_cli(
            ["--manifest", path, "--action", "plan"])

        self.assertEqual((code, err), (0, ""))
        plan = json.loads(out)
        self.assertEqual(plan["schema_version"], "pb-dispatch-plan-v2")
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(len(plan["next_batches"]), 1)

    def test_cli_fallback_outputs_sequential_order(self):
        path = self._write_manifest(stage_dag._creation_template_manifest())
        code, out, err = self._run_cli(
            ["--manifest", path, "--action", "fallback"])

        self.assertEqual((code, err), (0, ""))
        fallback = json.loads(out)
        self.assertEqual(
            fallback["schema_version"],
            "pb-dispatch-plan-v2",
        )
        self.assertEqual(fallback["mode"], "sequential")
        self.assertEqual(len(fallback["next_batches"]), 1)
        self.assertEqual(
            fallback["next_batches"][0]["stages"][0]["stage_id"],
            "session_init",
        )
        self.assertEqual(
            fallback["next_batches"][0]["stages"][0]["assignee"],
            "pb-intake-agent",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
