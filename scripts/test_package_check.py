import json
import tempfile
import unittest
from pathlib import Path

from package_check import build_package_report


ROOT = Path(__file__).resolve().parents[1]


class PackageCheckTests(unittest.TestCase):
    def test_real_repo_report_is_valid(self):
        report = build_package_report(ROOT)
        self.assertEqual(report.version, "0.3.0")
        self.assertIn(".codex-plugin/plugin.json", report.files)
        self.assertIn("config/plugin-contract.json", report.files)
        self.assertTrue(report.ok, report.errors)

    def test_forbidden_credential_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin = Path(temp) / "plugins" / "picturebook-screenwriter"
            (plugin / ".codex-plugin").mkdir(parents=True)
            (plugin / ".codex-plugin" / "plugin.json").write_text(
                json.dumps({"version": "0.3.0"}),
                encoding="utf-8",
            )
            (plugin / ".env").write_text("SHOULD_NOT_EXIST=1", encoding="utf-8")
            report = build_package_report(Path(temp))
            self.assertFalse(report.ok)
            self.assertTrue(any(".env" in error for error in report.errors))

    def test_generated_directories_are_excluded(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin = Path(temp) / "plugins" / "picturebook-screenwriter"
            (plugin / ".codex-plugin").mkdir(parents=True)
            (plugin / ".codex-plugin" / "plugin.json").write_text(
                json.dumps({"version": "0.3.0"}),
                encoding="utf-8",
            )
            cache = plugin / "skills" / "demo" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "demo.pyc").write_bytes(b"cache")
            report = build_package_report(Path(temp))
            self.assertFalse(any("__pycache__" in file for file in report.files))

    def test_json_token_fields_must_be_placeholders(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin = Path(temp) / "plugins" / "picturebook-screenwriter"
            (plugin / ".codex-plugin").mkdir(parents=True)
            (plugin / "config").mkdir()
            (plugin / ".codex-plugin" / "plugin.json").write_text(
                json.dumps({"version": "0.3.0"}),
                encoding="utf-8",
            )
            (plugin / "config" / "feishu-knowledge-base.example.json").write_text(
                json.dumps({"target": {"root_token": "live-wiki-token"}}),
                encoding="utf-8",
            )
            report = build_package_report(Path(temp))
            self.assertFalse(report.ok)
            self.assertTrue(any("root_token" in error for error in report.errors))


if __name__ == "__main__":
    unittest.main()
