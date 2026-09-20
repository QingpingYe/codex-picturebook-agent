import json
import tempfile
import unittest
from pathlib import Path

from governance_check import check_repo


def write_plugin(root: Path, contract_skills: list[str]) -> Path:
    plugin = root / "plugins" / "picturebook-screenwriter"
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / "config").mkdir()
    (plugin / "skills" / "picturebook-screenwriter").mkdir(parents=True)
    (plugin / "skills" / "picturebook-screenwriter" / "SKILL.md").write_text(
        "# entry",
        encoding="utf-8",
    )
    (plugin / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"version": "0.3.0"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (plugin / "config" / "plugin-contract.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entry_skill": "picturebook-screenwriter",
                "skills": contract_skills,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return plugin


class GovernanceCheckTests(unittest.TestCase):
    def test_missing_plugin_manifest_is_error(self):
        with tempfile.TemporaryDirectory() as temp:
            report = check_repo(Path(temp))
            self.assertFalse(report.ok)
            self.assertTrue(any("plugin.json" in error for error in report.errors))

    def test_missing_contract_is_error(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin = Path(temp) / "plugins" / "picturebook-screenwriter"
            (plugin / ".codex-plugin").mkdir(parents=True)
            (plugin / ".codex-plugin" / "plugin.json").write_text(
                "{}",
                encoding="utf-8",
            )
            report = check_repo(Path(temp))
            self.assertFalse(report.ok)
            self.assertTrue(
                any("plugin-contract.json" in error for error in report.errors)
            )

    def test_installed_and_contract_skills_must_match(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin = write_plugin(root, ["picturebook-screenwriter"])
            extra = plugin / "skills" / "extra"
            extra.mkdir()
            (extra / "SKILL.md").write_text("# extra", encoding="utf-8")
            report = check_repo(root)
            self.assertFalse(report.ok)
            self.assertTrue(
                any("skill set mismatch" in error for error in report.errors)
            )

    def test_valid_local_plugin_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            write_plugin(Path(temp), ["picturebook-screenwriter"])
            report = check_repo(Path(temp))
            self.assertEqual(report.errors, ())
            self.assertTrue(report.ok)


if __name__ == "__main__":
    unittest.main()
