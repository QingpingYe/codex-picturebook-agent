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

    def test_entry_records_built_against(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("built_against", text)
        self.assertIn("权威知识缺失", text)

    def test_entry_warns_when_knowledge_is_stale(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("陈旧", text)
        self.assertIn("revision_id", text)

    def test_entry_and_loader_name_operational_knowledge_loop_modules(self):
        entry = ENTRY_SKILL.read_text(encoding="utf-8")
        loader = ROOT / "skills" / "knowledge-loader" / "SKILL.md"
        loader_text = loader.read_text(encoding="utf-8")
        for name in (
            "AuthorityLoader",
            "build_dependency_record",
            "check_collisions",
            "find_stale_dependencies",
        ):
            with self.subTest(name=name):
                self.assertIn(name, entry)
                self.assertIn(name, loader_text)

    def test_entry_documents_built_against_append_and_reload(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("正文末尾", text)
        self.assertIn("parse_dependency_record", text)

    def test_illustration_route_requires_staging_and_confirmation(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("staging-planner", text)
        self.assertIn("image-prompt-architect", text)
        self.assertIn("illustration-export", text)
        self.assertIn("明确确认", text)

    def test_entry_routes_explicit_session_export(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("session-export", text)
        self.assertIn("绝对输出目录", text)
        self.assertIn("不得默认选择路径", text)

    def test_entry_blocks_project_failures(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("项目权威 FAIL", text)
        self.assertIn("阻断", text)

    def test_entry_distinguishes_craft_warnings(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("工艺基准 FAIL", text)
        self.assertIn("用户确认", text)

    def test_entry_integrates_wiki_and_optional_lexile_gates(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("Wiki lint", text)
        self.assertIn("lexile-check", text)
        self.assertIn("实测", text)

    def test_skills_document_conditional_revision_workflow(self):
        stage_skill = (
            ROOT / "skills" / "stage-orchestration" / "SKILL.md"
        ).read_text(encoding="utf-8")
        entry_skill = ENTRY_SKILL.read_text(encoding="utf-8")

        self.assertIn("waiting_for_user", stage_skill)
        self.assertIn("revision_requested", stage_skill)
        self.assertIn("不得派发子 Agent", stage_skill)
        self.assertIn("重新执行 preflight、collision_check 和 qa", entry_skill)

    def test_plugin_advertises_authoritative_feishu_knowledge(self):
        import json
        manifest = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
        self.assertIn("多人协作飞书知识库同步与权威检索", manifest["interface"]["longDescription"])


if __name__ == "__main__":
    unittest.main()
