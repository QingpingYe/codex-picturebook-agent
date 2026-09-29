#!/usr/bin/env python3
"""Static current-protocol checks for KB-AI ingest documentation."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
INGEST = ROOT / "plugins/picturebook-screenwriter/skills/wiki-ingest"
SKILL = INGEST / "SKILL.md"
TEMPLATES = INGEST / "references/structured-output-templates.md"
EXTRACTION = INGEST / "references/feishu-wiki-extraction.md"


class KB_AI_IngestDocsTests(unittest.TestCase):

    def test_no_runtime_document_claims_sync_constraint_rebuild(self):
        skill = SKILL.read_text(encoding="utf-8")
        templates = TEMPLATES.read_text(encoding="utf-8")
        self.assertNotIn("同步约束清单步骤", templates)
        self.assertNotIn("同步约束清单", skill)

    def test_redline_terms_section_is_marked_template_only(self):
        templates = TEMPLATES.read_text(encoding="utf-8")
        self.assertIn("模板约定", templates)
        self.assertIn("未实现", templates)
        self.assertIn("redline_terms", templates)

    def test_absent_sync_constraint_script_is_not_referenced(self):
        for path in (SKILL, TEMPLATES, EXTRACTION):
            with self.subTest(path=path.name):
                self.assertNotIn("sync_constraint_data.py", path.read_text(encoding="utf-8"))


    def test_corrections_promote_is_marked_unimplemented(self):
        templates = TEMPLATES.read_text(encoding="utf-8")
        self.assertIn("corrections-promote", templates)
        self.assertIn("本仓未实现", templates)

    def test_corrections_candidate_does_not_claim_empty_template_replacement(self):
        combined = TEMPLATES.read_text(encoding="utf-8") + EXTRACTION.read_text(encoding="utf-8")
        self.assertIn("不得以空模板替换", combined)

    def test_future_correction_append_requires_remote_read(self):
        combined = TEMPLATES.read_text(encoding="utf-8") + EXTRACTION.read_text(encoding="utf-8")
        self.assertIn("先读取当前远端权威页", combined)

    def test_corrections_redline_terms_are_preserved(self):
        templates = TEMPLATES.read_text(encoding="utf-8")
        self.assertIn("保留已有红线和词表", templates)

    def test_force_full_docs_do_not_claim_settings_key(self):
        combined = (
            SKILL.read_text(encoding="utf-8")
            + EXTRACTION.read_text(encoding="utf-8")
            + TEMPLATES.read_text(encoding="utf-8")
        )
        self.assertNotIn("forceFullSync", combined)

    def test_no_fixed_workbuddy_command_count_is_stated(self):
        combined = (
            SKILL.read_text(encoding="utf-8")
            + EXTRACTION.read_text(encoding="utf-8")
            + TEMPLATES.read_text(encoding="utf-8")
        )
        self.assertNotIn("13 条命令", combined)
        self.assertNotIn("13条命令", combined)


if __name__ == "__main__":
    unittest.main()