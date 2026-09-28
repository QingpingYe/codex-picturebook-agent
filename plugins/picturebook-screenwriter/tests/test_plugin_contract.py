import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import stage_dag


CONTRACT = ROOT / "config" / "plugin-contract.json"
ENTRY_SKILL = ROOT / "skills" / "picturebook-screenwriter" / "SKILL.md"
MANIFEST = ROOT / ".codex-plugin" / "plugin.json"
PLUGIN_README = ROOT / "README.md"
REPO_README = ROOT.parent.parent / "README.md"
ENFORCEMENT = ROOT.parent.parent / "docs" / "ENFORCEMENT.md"


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
                "project_authority": "any_finding_blocks_confirmation",
                "craft_benchmark": "FAIL_requires_user_confirmation",
                "wiki_lint": "read_only",
                "lexile_check": "optional_measured_only",
            },
        )

    def test_stage_workflow_uses_runtime_owner(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(
            contract["stage_workflow"],
            {
                "run_schema": stage_dag.RUN_SCHEMA,
                "runtime_source": "scripts/stage_dag.py",
            },
        )

    def test_enforcement_matrix_documents_levels(self):
        text = ENFORCEMENT.read_text(encoding="utf-8")
        for level in ("runtime_required", "script_checked", "prompt_only"):
            with self.subTest(level=level):
                self.assertIn(level, text)
        self.assertIn(
            "only applies when its owner script is in the execution path",
            text,
        )

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


class JevExecutionChoiceContractTests(unittest.TestCase):
    def _contract(self):
        return json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_contract_declares_execution_choice_gate(self):
        self.assertEqual(
            self._contract()["execution_choice"],
            {
                "gate_position": "before_intent_classification",
                "question_scope": "once_per_root_run",
                "modes": ["llm", "jev_assisted"],
                "pending_state": "waiting_for_execution_choice",
                "decision_context_schema": "pb-decision-context-v1",
            },
        )

    def test_contract_declares_jev_runtime_boundary(self):
        runtime = self._contract()["jev_runtime"]
        self.assertEqual(runtime["endpoint"], "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(runtime["credential_env"], "TYPESAFE_API_KEY")
        self.assertEqual(runtime["credential_sources"], ["environment"])
        self.assertEqual(runtime["key_logging"], "forbidden")
        self.assertEqual(runtime["base_url_override"], "forbidden")
        self.assertEqual(runtime["redirects"], "disabled")
        self.assertEqual(runtime["pinned_model"], "jev-1.13.0")
        self.assertEqual(runtime["moving_model_aliases"], "forbidden")
        self.assertEqual(
            runtime["operations"], ["knowledge_relevance", "text_quality_prefilter"]
        )

    def test_contract_registers_the_runtime_skill(self):
        self.assertIn("jev-decision-runtime", self._contract()["skills"])

    def test_execution_choice_schema_matches_the_runtime_constant(self):
        self.assertEqual(
            self._contract()["execution_choice"]["decision_context_schema"],
            stage_dag.DECISION_CONTEXT_SCHEMA,
        )


class JevPhase1DocumentationTests(unittest.TestCase):
    def test_enforcement_matrix_documents_the_jev_gates(self):
        text = ENFORCEMENT.read_text(encoding="utf-8")
        for required in (
            "jev_runner.py",
            "jev_client.py",
            "waiting_for_jev_key",
            "outcome_unknown",
            "model_version_mismatch",
            "request.json",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_enforcement_matrix_does_not_overclaim_the_lease(self):
        text = ENFORCEMENT.read_text(encoding="utf-8")
        self.assertIn("不提供跨主机互斥", text)

    def test_enforcement_matrix_does_not_overclaim_the_credential_gate(self):
        # No row may promise that a value is never echoed: argparse still prints
        # the value of a *recognised* option whose value it rejects, so the
        # unconditional claim would describe a protection the code does not
        # have. What both rows do have to name is the sharper guarantee that now
        # sits next to the gate — every argument the parser cannot place is
        # refused by name — and the residue that makes the warning worth keeping.
        rows = [
            line for line in ENFORCEMENT.read_text(encoding="utf-8").splitlines()
            if line.startswith("|")
            and "credential argument" in line.split("|")[1].lower()
        ]
        self.assertGreaterEqual(len(rows), 2)
        for row in rows:
            with self.subTest(row=row.split("|")[1].strip()):
                self.assertNotIn("never echoed", row)
                self.assertIn("argparse", row)
                self.assertIn("parse_known_options", row)

    def test_plugin_readme_documents_the_jev_credential_environment(self):
        text = PLUGIN_README.read_text(encoding="utf-8")
        self.assertIn("TYPESAFE_API_KEY", text)
        self.assertIn("jev-decision-runtime", text)
        self.assertIn("不得把密钥粘贴到对话中", text)


class JevPhase2DocumentationTests(unittest.TestCase):
    def test_plugin_readme_documents_relevance_screening(self):
        text = PLUGIN_README.read_text(encoding="utf-8")
        self.assertIn("knowledge_relevance", text)
        self.assertIn("硬约束块永不被过滤", text)

    def test_enforcement_matrix_documents_the_screening_gates(self):
        text = ENFORCEMENT.read_text(encoding="utf-8")
        for required in (
            "required_marking.py",
            "recall.py",
            "dependency_bundle",
            "run_operation",
            "more than one call to continue",
            "re-sends a record it cannot read",
            "attempt_status",
            "reused only for the request it answered",
            "blocked_records",
            "clears only its own pending call",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)


class JevPhase3DocumentationTests(unittest.TestCase):
    def test_pre_output_skill_names_the_split_finding_sources(self):
        text = (ROOT / "skills" / "pre_output-baseline" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("project_proxy", text)
        self.assertIn("project_authority", text)

    def test_pre_output_skill_stops_overclaiming_its_scan_coverage(self):
        text = (ROOT / "skills" / "pre_output-baseline" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("称呼一致性和插画描述做零假阴性扫描", text)
        self.assertIn("尚未实现", text)

    def test_pre_output_skill_names_the_facts_the_code_provides(self):
        text = (ROOT / "skills" / "pre_output-baseline" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("字数、句长、重复次数由代码算好放进 state", text)
        for fact in ("char_count", "sentence_count", "max_line_repeat"):
            with self.subTest(fact=fact):
                self.assertIn(fact, text)

    def test_enforcement_matrix_documents_the_screening_gates(self):
        text = ENFORCEMENT.read_text(encoding="utf-8")
        for required in ("screening.py", "redline_catalog.py", "screening_runner.py",
                         "promoted", "screened_clear", "may_skip_llm_review",
                         "blocked_records"):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_contract_declares_the_prefilter_operation(self):
        runtime = json.loads(CONTRACT.read_text(encoding="utf-8"))["jev_runtime"]
        self.assertIn("text_quality_prefilter", runtime["operations"])

    def test_pre_output_skill_documents_the_screening_entry_point(self):
        text = (ROOT / "skills" / "pre_output-baseline" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("screening_cli.py", text)
        self.assertIn("--escalation-out", text)
        self.assertIn("catalog_gap", text)

    def test_enforcement_matrix_documents_the_pre_screen_entry_point(self):
        text = ENFORCEMENT.read_text(encoding="utf-8")
        self.assertIn("screening_cli.py", text)
        self.assertIn("item_count = 0", text)


class JevPhase4DocumentationTests(unittest.TestCase):
    def test_contract_declares_the_comparison_boundary(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        comparison = contract["jev_comparison"]
        self.assertEqual(comparison["cc_switch_access"], "forbidden")
        self.assertEqual(comparison["llm_usage_source"], "manual_entry")
        self.assertEqual(comparison["export_gate"], "explicit_user_request")
        self.assertEqual(comparison["output_dir"], "user_supplied_absolute")
        self.assertEqual(comparison["calibration_change"], "human_only")

    def test_runtime_skill_documents_the_comparison(self):
        text = (ROOT / "skills" / "jev-decision-runtime" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("## 对比验收", text)
        self.assertIn("不读 CC Switch", text)

    def test_enforcement_matrix_documents_the_comparison_gates(self):
        text = ENFORCEMENT.read_text(encoding="utf-8")
        for required in ("comparison.py", "compare_cli.py", "cc-switch", "comparable"):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_the_procedure_reference_exists(self):
        path = ROOT / "skills" / "jev-decision-runtime" / "references" / "comparison-procedure.md"
        self.assertTrue(path.is_file())
        self.assertIn("CC Switch", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
