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
        self.assertEqual(len(manifest["stages"]), 12)

    def test_build_dispatch_plan_returns_correct_batch_count(self):
        manifest = stage_dag._creation_template_manifest()
        plan = stage_dag_codex.build_dispatch_plan(manifest)

        self.assertEqual(plan["total_batches"], 9)
        self.assertEqual(plan["total_stages"], 12)
        self.assertEqual(
            [batch["batch_index"] for batch in plan["batches"]],
            list(range(9)),
        )
        self.assertEqual(
            plan["batches"][0]["stages"][0]["stage_id"], "session_init")
        self.assertEqual(
            {item["stage_id"] for item in plan["batches"][1]["stages"]},
            {"brief_gate", "knowledge_load"},
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

    def test_build_dispatch_plan_sequential_mode(self):
        manifest = stage_dag._creation_template_manifest()
        manifest["mode"] = "light"
        manifest["stages"] = []
        plan = stage_dag_codex.build_dispatch_plan(manifest)

        self.assertEqual(plan["batches"], [])
        self.assertEqual(plan["total_batches"], 0)
        self.assertEqual(plan["total_stages"], 0)
        self.assertEqual(plan["mode"], "sequential")

    def test_cli_plan_outputs_json(self):
        path = self._write_manifest(stage_dag._creation_template_manifest())
        code, out, err = self._run_cli(
            ["--manifest", path, "--action", "plan"])

        self.assertEqual((code, err), (0, ""))
        plan = json.loads(out)
        self.assertEqual(plan["total_batches"], 9)
        self.assertEqual(plan["total_stages"], 12)

    def test_cli_fallback_outputs_sequential_order(self):
        path = self._write_manifest(stage_dag._creation_template_manifest())
        code, out, err = self._run_cli(
            ["--manifest", path, "--action", "fallback"])

        self.assertEqual((code, err), (0, ""))
        fallback = json.loads(out)
        self.assertEqual(fallback["mode"], "sequential")
        self.assertEqual(fallback["total_batches"], 9)
        self.assertEqual(
            [batch["batch_index"] for batch in fallback["batches"]],
            list(range(9)),
        )
        self.assertEqual(
            fallback["batches"][0]["stages"][0]["stage_id"], "session_init")
        self.assertEqual(
            fallback["batches"][0]["stages"][0]["assignee"],
            "pb-intake-agent",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
