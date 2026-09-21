import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / ".codex-plugin" / "plugin.json"
CONTRACT = ROOT / "config" / "plugin-contract.json"
PLUGIN_README = ROOT / "README.md"
REPO_README = ROOT.parent.parent / "README.md"


class ReleaseSurfaceTests(unittest.TestCase):
    def test_manifest_version_marks_phase5_minor_release(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0.3.1")

    def test_manifest_describes_completed_art_phase(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertIn("gated illustration workflow", manifest["description"])
        self.assertIn(
            "为已批准的绘本脚本生成结构化插画提示词",
            manifest["interface"]["defaultPrompt"],
        )
        self.assertIn("HTML 预览", manifest["interface"]["longDescription"])

    def test_contract_declares_phase5_artifacts(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

        for artifact in (
            "illustration_prompt_payload",
            "image_asset",
            "asset_registry",
            "html_preview",
        ):
            with self.subTest(artifact=artifact):
                self.assertIn(artifact, contract["artifact_types"])

    def test_plugin_readme_no_longer_marks_art_as_unsupported(self):
        text = PLUGIN_README.read_text(encoding="utf-8")

        self.assertIn("image-generate", text)
        self.assertIn("illustration-export", text)
        self.assertNotIn("- 插画与素材图生成\n", text)
        self.assertNotIn("- HTML 预览导出\n", text)

    def test_repo_readme_describes_self_contained_preview(self):
        text = REPO_README.read_text(encoding="utf-8")

        self.assertIn("Gated illustration workflow", text)
        self.assertIn("Self-contained HTML preview", text)


class ReleaseVersionTests(unittest.TestCase):
    def test_manifest_version_is_semver(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertIsNotNone(re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"]))

    def test_release_target_is_0_3_1(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0.3.1")

    def test_changelog_records_phase5(self):
        text = (ROOT.parent.parent / "docs" / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## [Unreleased]", text)
        self.assertIn("Gated illustration workflow", text)
        self.assertIn("Self-contained HTML preview", text)


class ReleaseChecklistTests(unittest.TestCase):
    def test_release_checklist_names_all_gates(self):
        text = (ROOT.parent.parent / "docs" / "RELEASE.md").read_text(encoding="utf-8")
        for gate in (
            "test_*.py",
            "generate.test.js",
            "run_regression.js",
            "governance_check.py",
            "package_check.py",
            "validate_plugin.py",
            "live Feishu",
            "marketplace",
        ):
            with self.subTest(gate=gate):
                self.assertIn(gate, text)

    def test_readme_documents_governance_commands(self):
        text = (ROOT.parent.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("python .\\scripts\\governance_check.py", text)
        self.assertIn("python .\\scripts\\package_check.py", text)

    def test_readme_documents_workspace_config_discovery(self):
        text = PLUGIN_README.read_text(encoding="utf-8")
        for value in (
            "feishu-knowledge-base.json",
            "PICTUREBOOK_KB_CONFIG",
            "authority_cli.py",
            "config-status",
            "非权威",
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_loader_skill_documents_explicit_offline_opt_in(self):
        text = (ROOT / "skills" / "knowledge-loader" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("authority_cli.py", text)
        self.assertIn("--allow-offline-cache", text)
        self.assertIn("非权威", text)


class LiveAcceptanceTests(unittest.TestCase):
    def test_live_acceptance_names_required_evidence(self):
        path = (
            ROOT.parent.parent
            / "docs"
            / "superpowers"
            / "plans"
            / "2026-09-18-06-live-acceptance.md"
        )
        text = path.read_text(encoding="utf-8")
        for evidence in (
            "User A",
            "User B",
            "revision_id",
            "human edit survives",
            "only one lease",
            "explicit user approval",
        ):
            with self.subTest(evidence=evidence):
                self.assertIn(evidence, text)


if __name__ == "__main__":
    unittest.main()
