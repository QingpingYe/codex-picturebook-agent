import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
CONTRACT = ROOT / "config" / "plugin-contract.json"
ENTRY_SKILL = ROOT / "skills" / "picturebook-screenwriter" / "SKILL.md"
MANIFEST = ROOT / ".codex-plugin" / "plugin.json"
PLUGIN_README = ROOT / "README.md"
REPO_README = ROOT.parent.parent / "README.md"


class PluginContractTests(unittest.TestCase):
    def test_contract_schema_is_declared(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(contract["schema_version"], 1)
        self.assertEqual(contract["entry_skill"], "picturebook-screenwriter")

    def test_contract_matches_installed_skills(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        installed = {
            path.name for path in (ROOT / "skills").iterdir()
            if path.is_dir() and (path / "SKILL.md").exists()
        }
        self.assertEqual(set(contract["skills"]), installed)

    def test_entry_skill_declares_every_intent(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        for intent in ("creation", "revision", "review", "knowledge", "illustration"):
            with self.subTest(intent=intent):
                self.assertIn(intent, text)

    def test_entry_skill_declares_confirmation_and_export_gates(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("确认门", text)
        self.assertIn("明确要求导出", text)

    def test_entry_skill_forbids_implicit_file_writes(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("不得默认落盘", text)
        self.assertIn("用户明确批准", text)

    def test_manifest_matches_contract_boundary(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertIn("多人协作飞书知识库同步与权威检索", manifest["interface"]["longDescription"])
        self.assertNotIn("WorkBuddy TeamCreate", manifest["interface"]["longDescription"])

    def test_plugin_readme_matches_current_capabilities(self):
        text = PLUGIN_README.read_text(encoding="utf-8")
        self.assertIn("Codex 原生", text)
        self.assertIn("不依赖 WorkBuddy 团队运行时", text)
        self.assertNotIn("外部知识库同步", text)

    def test_repo_readme_matches_current_capabilities(self):
        text = REPO_README.read_text(encoding="utf-8")
        self.assertIn("Codex 原生", text)
        self.assertIn("不依赖 WorkBuddy 团队运行时", text)


if __name__ == "__main__":
    unittest.main()
