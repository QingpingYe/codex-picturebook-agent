"""Tests for the comparison report CLI and its export gate."""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "skills" / "session-export" / "scripts"))

from compare_cli import (
    OUT_OF_PLUGIN_ERROR,
    main,
    require_explicit_output_dir,
)
from comparison import COMPARISON_SCHEMA
from decision_contract import default_policy_path
from jev_runner import write_atomic

from test_comparison import LLM_USAGE, identity, trace


class OutputDirGuardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.plugin = self.root / "plugin"
        self.plugin.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_relative_directory_is_refused(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            require_explicit_output_dir(Path("exports"), self.plugin)

    def test_the_plugin_directory_is_refused(self):
        with self.assertRaisesRegex(ValueError, "outside the plugin"):
            require_explicit_output_dir(self.plugin, self.plugin)

    def test_a_directory_inside_the_plugin_is_refused(self):
        inside = self.plugin / "reports"
        with self.assertRaisesRegex(ValueError, "outside the plugin"):
            require_explicit_output_dir(inside, self.plugin)

    def test_an_absolute_directory_outside_the_plugin_is_accepted(self):
        outside = self.root / "exports"
        self.assertEqual(require_explicit_output_dir(outside, self.plugin),
                         outside.resolve())


class CompareCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.run_dir = self.root / "run"
        write_atomic(
            self.run_dir / "jev" / "text_quality_prefilter" / "trace" / "0001.json",
            trace("text_quality_prefilter"),
        )
        self.usage = self.root / "llm-usage.json"
        self.usage.write_text(json.dumps(LLM_USAGE, ensure_ascii=False),
                              encoding="utf-8")
        self.out_dir = self.root / "exports"
        self.out_dir.mkdir()
        self.plugin = self.root / "plugin"
        self.plugin.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _argv(self, *extra):
        return [
            "--run-dir", str(self.run_dir),
            "--llm-usage", str(self.usage),
            "--output-dir", str(self.out_dir),
            "--plugin-root", str(self.plugin),
            "--policy", str(default_policy_path()),
            *extra,
        ]

    def _main(self, argv, stdout=None):
        out = io.StringIO() if stdout is None else stdout
        err = io.StringIO()
        code = main(argv, stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_without_an_output_directory_only_json_is_printed(self):
        code, out, _ = self._main([
            "--run-dir", str(self.run_dir), "--llm-usage", str(self.usage),
            "--policy", str(default_policy_path()),
        ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["schema_version"], COMPARISON_SCHEMA)
        self.assertFalse(list(self.root.rglob("*-audit")))

    def test_an_explicit_output_directory_writes_both_renderings(self):
        code, out, _ = self._main(self._argv())
        self.assertEqual(code, 0)
        bundle = self.out_dir / "path-comparison-audit"
        self.assertTrue((bundle / "comparison.json").is_file())
        self.assertTrue((bundle / "comparison.md").is_file())
        self.assertTrue((bundle / "manifest.json").is_file())
        self.assertEqual(
            json.loads((bundle / "comparison.json").read_text(encoding="utf-8"))["schema_version"],
            COMPARISON_SCHEMA,
        )

    def test_a_relative_output_directory_exits_with_a_usage_error(self):
        code, _, err = self._main([
            "--run-dir", str(self.run_dir), "--llm-usage", str(self.usage),
            "--output-dir", "exports", "--plugin-root", str(self.plugin),
            "--policy", str(default_policy_path()),
        ])
        self.assertEqual(code, 1)
        self.assertIn("absolute", err)

    def test_writing_into_the_plugin_is_refused(self):
        code, _, err = self._main([
            "--run-dir", str(self.run_dir), "--llm-usage", str(self.usage),
            "--output-dir", str(self.plugin), "--plugin-root", str(self.plugin),
            "--policy", str(default_policy_path()),
        ])
        self.assertEqual(code, 1)
        self.assertIn(OUT_OF_PLUGIN_ERROR, err)

    def test_credential_arguments_are_refused_without_echoing_the_value(self):
        out, err = io.StringIO(), io.StringIO()
        code = main(
            ["--api-key=SUPER-SECRET", "--run-dir", str(self.run_dir)],
            stdout=out, stderr=err,
        )
        self.assertEqual(code, 2)
        self.assertNotIn("SUPER-SECRET", err.getvalue())
        self.assertNotIn("SUPER-SECRET", out.getvalue())

    def test_the_refusal_is_written_to_the_injected_stream(self):
        # The test above passes even with the credential gate removed: argparse
        # reports an unknown flag on the process stderr rather than on the
        # stream this CLI was handed, so `err` stays empty either way and the
        # exit code is 2 either way. Pinning the refusal text to the injected
        # stream is what makes the gate itself falsifiable.
        out, err = io.StringIO(), io.StringIO()
        code = main(["--api-key=SECRET-VALUE"], stdout=out, stderr=err)
        self.assertEqual(code, 2)
        self.assertIn(
            "credentials are not accepted on the command line", err.getvalue()
        )
        self.assertNotIn("SECRET-VALUE", err.getvalue())

    def test_no_written_file_contains_a_credential_field_name(self):
        self._main(self._argv())
        for path in (self.out_dir / "path-comparison-audit").rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                for forbidden in ("api_key", "authorization", "password", "secret"):
                    self.assertNotIn(forbidden, text.lower())

    def test_usage_errors_exit_two(self):
        code, _, _ = self._main([])
        self.assertEqual(code, 2)

    def test_a_missing_llm_usage_file_is_reported_as_an_error(self):
        code, _, err = self._main([
            "--run-dir", str(self.run_dir),
            "--llm-usage", str(self.root / "nope.json"),
            "--policy", str(default_policy_path()),
        ])
        self.assertEqual(code, 1)
        self.assertIn("error", err.lower())


if __name__ == "__main__":
    unittest.main()
