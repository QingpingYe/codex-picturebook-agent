#!/usr/bin/env python3
"""check_delta.py 远端 source baseline 离线单测。"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_delta as cd  # noqa: E402

EDIT = 1756572300000


def snap_node(token, obj_type="file", edit_time_ms=EDIT, revision_id="r1"):
    return {
        "title": token,
        "obj_token": f"obj-{token}",
        "obj_type": obj_type,
        "ext": ".docx",
        "edit_time_ms": edit_time_ms,
        "revision_id": revision_id,
    }


def entry(token, revision="r1", edit_time_ms=EDIT):
    return {
        "key": f"s/p/{token}",
        "source_revisions": {token: revision},
        "source_edit_times": {token: edit_time_ms},
    }


def baseline(entries=None, admission=None):
    return {
        "schema_version": 1,
        "index_revision_id": 10,
        "entries": entries or [],
        "admission": admission,
    }


class TestBaseline(unittest.TestCase):

    def test_missing_baseline_is_first_run(self):
        result = cd.classify({"nodes": {"tok": snap_node("tok")}}, None)
        self.assertTrue(result["first_run"])
        self.assertEqual(result["verdicts"]["tok"]["verdict"], "first_run")

    def test_new_when_token_absent(self):
        result = cd.classify({"nodes": {"tok": snap_node("tok")}}, baseline([]))
        self.assertEqual(result["verdicts"]["tok"]["verdict"], "first_run")

    def test_matching_time_and_revision_is_unchanged(self):
        result = cd.classify(
            {"nodes": {"tok": snap_node("tok", obj_type="docx")}},
            baseline([entry("tok")]),
        )
        self.assertEqual(result["verdicts"]["tok"]["verdict"], "unchanged")

    def test_doc_revision_mismatch_forces_changed(self):
        result = cd.classify(
            {"nodes": {"tok": snap_node("tok", obj_type="docx", revision_id="r2")}},
            baseline([entry("tok")]),
        )
        self.assertEqual(result["verdicts"]["tok"]["verdict"], "changed")

    def test_file_without_revision_can_skip_with_time(self):
        node = snap_node("tok", obj_type="file", revision_id=None)
        result = cd.classify({"nodes": {"tok": node}}, baseline([entry("tok")]))
        self.assertEqual(result["verdicts"]["tok"]["verdict"], "unchanged")
        node["revision_id"] = "ignored"
        result = cd.classify({"nodes": {"tok": node}}, baseline([entry("tok")]))
        self.assertEqual(result["verdicts"]["tok"]["verdict"], "unchanged")

    def test_missing_index_time_forces_changed(self):
        item = entry("tok")
        item["source_edit_times"] = None
        result = cd.classify({"nodes": {"tok": snap_node("tok")}}, baseline([item]))
        self.assertEqual(result["verdicts"]["tok"]["verdict"], "changed")

    def test_conflicting_index_times_force_changed(self):
        result = cd.classify(
            {"nodes": {"tok": snap_node("tok")}},
            baseline([entry("tok", edit_time_ms=EDIT), entry("tok", edit_time_ms=EDIT + 1)]),
        )
        self.assertEqual(result["verdicts"]["tok"]["verdict"], "changed")

    def test_cache_loss_does_not_change_verdict(self):
        with tempfile.TemporaryDirectory() as empty:
            result1 = cd.classify({"nodes": {"tok": snap_node("tok")}}, baseline([entry("tok")]))
            result2 = cd.classify({"nodes": {"tok": snap_node("tok")}}, baseline([entry("tok")]), empty)
        self.assertEqual(result1["verdicts"]["tok"]["verdict"], "unchanged")
        self.assertEqual(result2["verdicts"]["tok"]["verdict"], "unchanged")

    def test_snapshot_time_missing_unknown(self):
        result = cd.classify(
            {"nodes": {"tok": snap_node("tok", edit_time_ms=None)}},
            baseline([entry("tok")]),
        )
        self.assertEqual(result["verdicts"]["tok"]["verdict"], "unknown")

    def test_deleted_from_index(self):
        result = cd.classify({"nodes": {}}, baseline([entry("gone")]))
        self.assertEqual(result["verdicts"]["gone"]["verdict"], "deleted")

    def test_only_never_infers_deleted(self):
        result = cd.classify(
            {"nodes": {"tok": snap_node("tok")}},
            baseline([entry("tok"), entry("gone")]),
            only=["tok"],
        )
        self.assertNotIn("gone", result["verdicts"])

    def test_only_empty_intersection_raises(self):
        with self.assertRaises(ValueError):
            cd.classify({"nodes": {"tok": snap_node("tok")}}, baseline([entry("tok")]), only=["missing"])

    def test_force_full_all_changed(self):
        result = cd.classify(
            {"nodes": {"tok": snap_node("tok")}},
            baseline([entry("tok")]),
            force_full=True,
        )
        self.assertEqual(result["verdicts"]["tok"]["verdict"], "changed")

    def test_schema_error_node_unknown(self):
        result = cd.classify({"nodes": {"tok": "bad"}}, baseline([entry("tok")]))
        self.assertEqual(result["verdicts"]["tok"]["verdict"], "unknown")

    def test_build_token_baseline_marks_incomplete(self):
        item = entry("tok")
        item["source_edit_times"] = None
        built = cd.build_token_baseline(baseline([item]))
        self.assertFalse(built["tok"]["complete"])


class TestContainers(unittest.TestCase):

    def test_container_is_counted_not_processed(self):
        result = cd.classify(
            {"nodes": {
                "container": {"has_child": True},
                "content": snap_node("content"),
            }},
            baseline([entry("content")]),
        )
        self.assertNotIn("container", result["verdicts"])
        self.assertEqual(result["summary"]["containers"], 1)

    def test_missing_has_child_is_not_container(self):
        result = cd.classify({"nodes": {"a": snap_node("a")}}, baseline([entry("a")]))
        self.assertIn("a", result["verdicts"])
        self.assertEqual(result["summary"]["containers"], 0)

    def test_container_never_receives_new_or_changed_verdict(self):
        result = cd.classify({"nodes": {"container": {"has_child": True}}}, baseline([]))
        self.assertNotIn("container", result["verdicts"])
        self.assertEqual(result["summary"]["new"], 0)
        self.assertEqual(result["summary"]["changed"], 0)

    def test_delta_line_displays_containers(self):
        summary = {
            "total": 0, "skip": 0, "process": 0, "new": 0,
            "changed": 0, "deleted": 0, "unknown": 0,
            "first_run": False, "containers": 2,
        }
        self.assertIn("containers 2", cd.build_summary_line(summary))

    def test_check_delta_has_no_container_body_download_path(self):
        root = Path(__file__).parents[1]
        skill = (root / "SKILL.md").read_text(encoding="utf-8")
        extraction = (root / "references/feishu-wiki-extraction.md").read_text(encoding="utf-8")
        self.assertIn("文件夹节点只递归，不下载容器正文", skill)
        self.assertIn("文件夹节点只递归，不下载容器正文", extraction)
        self.assertNotIn("pure_containers", skill)


class TestForceFullHelp(unittest.TestCase):

    def _help(self):
        import contextlib
        from io import StringIO

        stdout = StringIO()
        with contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as raised:
                cd.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        return stdout.getvalue()

    def test_force_full_help_requires_explicit_dispatch(self):
        self.assertIn("调度方显式传 --force-full", self._help())

    def test_force_full_help_does_not_claim_settings_key(self):
        self.assertNotIn("forceFullSync", self._help())


class TestNewSourcesAudit(unittest.TestCase):

    @staticmethod
    def _result(verdicts, first_run=False):
        return {"first_run": first_run, "verdicts": verdicts}

    def test_new_sources_zero_uses_fixed_line(self):
        result = self._result({"a": {"verdict": "unchanged"}})
        self.assertEqual(cd.build_new_sources_line(result), "NEW_SOURCES: 0")

    def test_new_sources_lists_content_tokens_sorted_and_unique(self):
        result = self._result({
            "b": {"verdict": "new"},
            "a": {"verdict": "new"},
            "c": {"verdict": "changed"},
        })
        self.assertEqual(cd.build_new_sources_line(result),
                         "NEW_SOURCES: 2 tokens=a,b")

    def test_first_run_does_not_count_as_new(self):
        result = self._result({"a": {"verdict": "first_run"}}, first_run=True)
        self.assertEqual(cd.build_new_sources_line(result), "NEW_SOURCES: 0")

    def test_containers_are_not_new_sources(self):
        result = self._result({"container": {"verdict": "container"}})
        self.assertEqual(cd.new_source_tokens(result), [])

    def test_excluded_nodes_are_not_new_sources(self):
        result = self._result({"excluded": {"verdict": "excluded"}})
        self.assertEqual(cd.new_source_tokens(result), [])

    def test_new_sources_line_is_emitted_on_fallback(self):
        import contextlib
        from io import StringIO

        stdout = StringIO()
        stderr = StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = cd.main(["--nodes", "missing.json"])
        self.assertEqual(rc, 1)
        self.assertIn("NEW_SOURCES: 0", stdout.getvalue())


class TestRetirement(unittest.TestCase):

    def test_retired_state_options_are_rejected(self):
        for option in ("--state", "--write-state", "--hash-map", "--revision-map"):
            with self.subTest(option=option):
                with self.assertRaises(SystemExit) as raised:
                    cd.main(["--nodes", "nodes.json", option, "old.json"])
                self.assertEqual(raised.exception.code, 2)

    def test_runtime_source_has_no_legacy_symbols(self):
        source = Path(__file__).with_name("check_delta.py").read_text(encoding="utf-8")
        for symbol in (
            "merge_state",
            "delta_state.json",
            "--write-state",
            "--hash-map",
            "--revision-map",
            "finalize 阶段",
        ):
            with self.subTest(symbol=symbol):
                self.assertNotIn(symbol, source)


class TestMain(unittest.TestCase):

    def test_plan_mode_writes_plan_file_and_prints_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = os.path.join(tmp, "nodes.json")
            baseline_path = os.path.join(tmp, "source_baseline.json")
            plan_path = os.path.join(tmp, "plan.json")
            with open(snapshot_path, "w", encoding="utf-8") as f:
                json.dump({"nodes": {"tok": snap_node("tok")}}, f)
            with open(baseline_path, "w", encoding="utf-8") as f:
                json.dump(baseline([entry("tok")]), f)
            rc = cd.main(["--nodes", snapshot_path, "--source-baseline", baseline_path,
                          "--out", plan_path])
            self.assertEqual(rc, 0)
            with open(plan_path, encoding="utf-8") as f:
                plan = json.load(f)
            self.assertEqual(plan["verdicts"]["tok"]["verdict"], "unchanged")


class TestWriteAtomic(unittest.TestCase):
    def test_write_atomic_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.json")
            cd.write_atomic(path, {"x": 1})
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"x": 1})


if __name__ == "__main__":
    unittest.main()