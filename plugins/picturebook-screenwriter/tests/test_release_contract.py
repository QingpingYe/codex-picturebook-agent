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

        self.assertEqual(manifest["version"], "0.3.0")

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


if __name__ == "__main__":
    unittest.main()
