"""Tests for the comparison report CLI and its export gate."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "skills" / "session-export" / "scripts"))

import compare_cli
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

    def test_the_root_the_script_itself_lives_in_is_checked_too(self):
        # The caller names `--plugin-root`, so the gate cannot rely on that
        # argument: a decoy root would otherwise let an export land inside the
        # plugin this file is installed in.
        installed = self.root / "installed"
        installed.mkdir()
        self.assertEqual(compare_cli.PLUGIN_ROOT,
                         Path(compare_cli.__file__).resolve().parents[3])
        with mock.patch.object(compare_cli, "PLUGIN_ROOT", installed):
            with self.assertRaisesRegex(ValueError, "outside the plugin"):
                require_explicit_output_dir(installed / "exports", self.plugin)
            with self.assertRaisesRegex(ValueError, "outside the plugin"):
                require_explicit_output_dir(installed / "deep" / "nested", self.plugin)
            with self.assertRaisesRegex(ValueError, "outside the plugin"):
                require_explicit_output_dir(installed, self.plugin)

    def test_installed_root_alias_is_checked_after_resolution(self):
        installed = self.root / "installed"
        installed.mkdir()
        with mock.patch.object(compare_cli, "PLUGIN_ROOT", installed / ".." / "installed"):
            with self.assertRaisesRegex(ValueError, "outside the plugin"):
                require_explicit_output_dir(installed / "exports", self.plugin)


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

    def test_the_exported_and_printed_reports_carry_the_same_export_path(self):
        # The copy in the bundle is the artifact of record, and it used to be
        # written before `exported_to` was filled in, so only stdout carried
        # the path while the audit copy silently omitted it.
        code, out, _ = self._main(self._argv())
        self.assertEqual(code, 0)
        bundle = self.out_dir / "path-comparison-audit"
        written = json.loads((bundle / "comparison.json").read_text(encoding="utf-8"))
        printed = json.loads(out)
        self.assertEqual(written["exported_to"], printed["exported_to"])
        self.assertEqual(Path(written["exported_to"]).name, bundle.name)

    def test_both_renderings_record_the_export_path(self):
        code, _, _ = self._main(self._argv())
        self.assertEqual(code, 0)
        bundle = self.out_dir / "path-comparison-audit"
        self.assertIn(str(bundle.resolve()),
                      (bundle / "comparison.md").read_text(encoding="utf-8"))

    def test_a_relative_output_directory_exits_with_a_usage_error(self):
        code, _, err = self._main([
            "--run-dir", str(self.run_dir), "--llm-usage", str(self.usage),
            "--output-dir", "exports", "--plugin-root", str(self.plugin),
            "--policy", str(default_policy_path()),
        ])
        self.assertEqual(code, 1)
        self.assertIn("absolute", err)

    def test_the_output_directory_is_checked_before_the_report_is_built(self):
        # A pre-flight: an unusable destination has to be reported as such, not
        # masked by whatever else this run would have failed on later.
        code, _, err = self._main([
            "--run-dir", str(self.root / "missing-run"),
            "--llm-usage", str(self.root / "missing-usage.json"),
            "--output-dir", "exports", "--plugin-root", str(self.plugin),
            "--policy", str(self.root / "missing-policy.json"),
        ])
        self.assertEqual(code, 1)
        self.assertIn("absolute", err)
        self.assertNotIn("policy", err)

    def test_a_missing_plugin_root_is_reported_before_the_report_is_built(self):
        code, _, err = self._main([
            "--run-dir", str(self.root / "missing-run"),
            "--llm-usage", str(self.root / "missing-usage.json"),
            "--output-dir", str(self.out_dir),
            "--policy", str(self.root / "missing-policy.json"),
        ])
        self.assertEqual(code, 1)
        self.assertIn("--plugin-root", err)
        self.assertNotIn("policy", err)

    def test_writing_into_the_plugin_is_refused(self):
        code, _, err = self._main([
            "--run-dir", str(self.run_dir), "--llm-usage", str(self.usage),
            "--output-dir", str(self.plugin), "--plugin-root", str(self.plugin),
            "--policy", str(default_policy_path()),
        ])
        self.assertEqual(code, 1)
        self.assertIn(OUT_OF_PLUGIN_ERROR, err)

    def test_a_spoofed_plugin_root_cannot_export_into_the_installed_plugin(self):
        installed = self.root / "installed"
        installed.mkdir()
        decoy = self.root / "decoy"
        decoy.mkdir()
        target = installed / "exports"
        with mock.patch.object(compare_cli, "PLUGIN_ROOT", installed):
            with mock.patch.object(compare_cli, "export_session") as writer:
                code, _, err = self._main([
                    "--run-dir", str(self.run_dir), "--llm-usage", str(self.usage),
                    "--output-dir", str(target), "--plugin-root", str(decoy),
                    "--policy", str(default_policy_path()),
                ])
        self.assertEqual(code, 1)
        self.assertIn(OUT_OF_PLUGIN_ERROR, err)
        # The refusal has to come before anything is created, and the bundle
        # writer must not even be reached: the directory was once made first and
        # only then rejected by the bundle's own gate.
        self.assertFalse(target.exists())
        writer.assert_not_called()

    def test_credential_arguments_are_refused_without_echoing_the_value(self):
        out, err = io.StringIO(), io.StringIO()
        code = main(
            ["--api-key=SUPER-SECRET", "--run-dir", str(self.run_dir)],
            stdout=out, stderr=err,
        )
        self.assertEqual(code, 2)
        self.assertNotIn("SUPER-SECRET", err.getvalue())
        self.assertNotIn("SUPER-SECRET", out.getvalue())

    def test_usage_errors_go_to_the_injected_stream(self):
        # argparse defaults to the process streams, which makes the `stderr`
        # argument a lie for any caller embedding this CLI: the message would
        # leak to the terminal while the injected stream stayed empty.
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(io.StringIO()) as leaked:
            code = main(["--bogus-flag"], stdout=out, stderr=err)
        self.assertEqual(code, 2)
        self.assertIn("usage: compare_cli.py", err.getvalue())
        self.assertIn("error:", err.getvalue())
        self.assertEqual(leaked.getvalue(), "")
        self.assertEqual(out.getvalue(), "")

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

    def test_an_unrecognised_option_is_refused_without_its_value(self):
        # This CLI turns argparse's own message onto the injected stream, so a
        # flag the credential vocabulary does not read — `--tokens` is a count,
        # not the token — would arrive with its value quoted. Refusing the
        # argument the parser cannot place, by name, is what closes that.
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(io.StringIO()) as leaked:
            code = main(
                ["--run-dir", str(self.run_dir), "--llm-usage", str(self.usage),
                 "--tokens=SUPER-SECRET-VALUE"],
                stdout=out, stderr=err,
            )
        self.assertEqual(code, 2)
        self.assertIn("unrecognized arguments: --tokens", err.getvalue())
        self.assertNotIn("SUPER-SECRET-VALUE", err.getvalue())
        self.assertNotIn("SUPER-SECRET-VALUE", out.getvalue())
        self.assertEqual(leaked.getvalue(), "")

    def test_an_abbreviated_flag_is_refused_without_quoting_its_value(self):
        # Abbreviations are off, so a shortened flag is an unrecognised argument
        # reported by name rather than an ambiguity that names the value it
        # could not resolve.
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(io.StringIO()) as leaked:
            code = main(
                ["--run-dir", str(self.run_dir), "--llm-usage", str(self.usage),
                 "--run-di=SUPER-SECRET-VALUE"],
                stdout=out, stderr=err,
            )
        self.assertEqual(code, 2)
        self.assertIn("--run-di", err.getvalue())
        self.assertNotIn("SUPER-SECRET-VALUE", err.getvalue())
        self.assertNotIn("SUPER-SECRET-VALUE", out.getvalue())
        self.assertEqual(leaked.getvalue(), "")

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
