import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
ENTRY_SKILL = ROOT / "skills" / "picturebook-screenwriter" / "SKILL.md"
PLUGIN_JSON = ROOT / ".codex-plugin" / "plugin.json"


class SkillContractTests(unittest.TestCase):
    def test_entry_routes_sync_to_store(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("feishu-knowledge-store", text)
        self.assertNotIn("If the user asks for Feishu sync", text)

    def test_entry_routes_knowledge_loading_before_drafting(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("knowledge-loader", text)

    def test_plugin_advertises_authoritative_feishu_knowledge(self):
        import json
        manifest = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
        self.assertIn("多人协作飞书知识库同步与权威检索", manifest["interface"]["longDescription"])


if __name__ == "__main__":
    unittest.main()
