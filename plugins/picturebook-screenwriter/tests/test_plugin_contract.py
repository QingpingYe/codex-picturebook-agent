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

    def test_contract_declares_creative_pipeline_skills(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        for skill in (
            "story-planning",
            "pre_create-baseline",
            "in_create-baseline",
            "post_create-baseline",
            "pre_output-baseline",
            "quality-baseline",
        ):
            with self.subTest(skill=skill):
                self.assertIn(skill, contract["skills"])

    def test_contract_declares_creative_pipeline_boundary(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        pipeline = contract["creative_pipeline"]
        self.assertEqual(pipeline["resolver"], "skills/picturebook-screenwriter/scripts/slot_resolver.py")
        self.assertEqual(
            pipeline["slots"],
            ["pre_create", "in_create", "post_create", "pre_output", "quality"],
        )
        self.assertEqual(pipeline["default_tier"], "baseline")
        for slot in pipeline["slots"]:
            with self.subTest(slot=slot):
                self.assertIn(pipeline["slot_skills"][slot], contract["skills"])
        self.assertEqual(
            pipeline["lightweight_mode"],
            {
                "skill": "story-planning",
                "trigger": "explicit_user_request",
                "write_policy": "dialog_only",
            },
        )

    def test_entry_skill_declares_confirmation_and_export_gates(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("确认门", text)
        self.assertIn("明确要求导出", text)

    def test_contract_declares_lexile_check_skill(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertIn("lexile-check", contract["skills"])

    def test_contract_declares_art_export_skills(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        for skill in (
            "image-prompt-architect",
            "image-generate",
            "illustration-export",
        ):
            with self.subTest(skill=skill):
                self.assertIn(skill, contract["skills"])

    def test_contract_declares_session_export(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertIn("session-export", contract["skills"])

    def test_plugin_readme_describes_session_export(self):
        text = PLUGIN_README.read_text(encoding="utf-8")
        self.assertIn("session-export", text)
        self.assertNotIn("- 会话取证导出\n", text)

    def test_contract_declares_gated_illustration_route(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        route = contract["illustration_route"]
        self.assertEqual(
            route["sequence"],
            [
                "staging-planner",
                "image-prompt-architect",
                "image-generate",
                "illustration-export",
            ],
        )
        self.assertEqual(route["confirmation_gate"], "before_image_generation")
        self.assertEqual(route["explicit_choices"], ["output_directory", "size", "quality"])

    def test_contract_declares_image_generation_boundary(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(
            contract["image_generation"],
            {
                "api_key_sources": [
                    "explicit_cli_argument",
                    "PICTUREBOOK_SFACAI_KEY",
                ],
                "key_logging": "forbidden",
                "default_api_call": "forbidden",
                "missing_references": "error",
            },
        )

    def test_contract_declares_html_export_boundary(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(
            contract["html_export"],
            {
                "self_contained": True,
                "model_calls": "forbidden",
                "external_resources": "forbidden",
                "embed_required_args": ["limit_mb", "quality"],
                "over_limit": "fail_closed",
            },
        )

    def test_contract_declares_quality_gate_boundary(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(
            contract["quality_gate"],
            {
                "confirmation_package": [
                    "draft",
                    "metrics",
                    "findings",
                    "blocked_reasons",
                ],
                "project_authority": "FAIL_blocks_confirmation",
                "craft_benchmark": "FAIL_requires_user_confirmation",
                "wiki_lint": "read_only",
                "lexile_check": "optional_measured_only",
            },
        )

    def test_contract_declares_conditional_stage_workflow(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(
            contract["stage_workflow"]["run_schema"],
            "pb-stage-run-v2",
        )
        self.assertEqual(
            contract["stage_workflow"]["confirmation_outcomes"],
            ["approved", "revision_requested", "cancelled"],
        )
        self.assertTrue(
            contract["stage_workflow"]["revision_reruns"]
            == ["preflight", "collision_check", "qa", "qa_synthesis"]
        )
        self.assertEqual(
            contract["stage_workflow"]["lead_owned_gates"],
            [
                "confirmation_gate",
                "asset_confirmation",
                "asset_final_confirmation",
            ],
        )
        self.assertTrue(
            contract["stage_workflow"]["skipped_is_terminal"])

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

    def test_plugin_readme_uses_current_multi_agent_boundary(self):
        text = PLUGIN_README.read_text(encoding="utf-8")
        self.assertNotIn("- 多 Agent / 子 Agent 团队执行\n", text)
        self.assertIn("可选阶段 DAG", text)
        self.assertIn("WorkBuddy", text)

    def test_entry_skill_uses_authoritative_feishu_knowledge(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertNotIn("use only local reference files in this MVP", text)
        self.assertIn("authoritative Feishu knowledge", text)

    def test_readme_marketplace_name_matches_manifest(self):
        marketplace = json.loads(
            (ROOT.parent.parent / ".agents" / "plugins" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        text = REPO_README.read_text(encoding="utf-8")
        self.assertIn(f"@{marketplace['name']}", text)


if __name__ == "__main__":
    unittest.main()
