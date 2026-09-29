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


if __name__ == "__main__":
    unittest.main()