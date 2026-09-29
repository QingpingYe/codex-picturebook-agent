#!/usr/bin/env python3
"""Final current-contract consistency checks for the KB-AI batches."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
COMPAT = ROOT / "docs/workbuddy-feishu-knowledge-base-compatibility.md"
STORE_SKILL = ROOT / "plugins/picturebook-screenwriter/skills/feishu-knowledge-store/SKILL.md"
INGEST_SKILL = ROOT / "plugins/picturebook-screenwriter/skills/wiki-ingest/SKILL.md"
TEMPLATES = ROOT / "plugins/picturebook-screenwriter/skills/wiki-ingest/references/structured-output-templates.md"
READONLY = ROOT / "docs/release-gates/2026-09-29-kb-ai-readonly-acceptance.md"
RELEASE_GATE = ROOT / "docs/release-gates/2026-09-29-kb-ai-release-gate.md"


def combined():
    return "\n".join(path.read_text(encoding="utf-8") for path in (
        COMPAT, STORE_SKILL, INGEST_SKILL, TEMPLATES,
    ))


class KB_AIContractConsistencyTests(unittest.TestCase):

    def test_required_control_tokens_are_documented(self):
        text = combined()
        for token in ("AI_KB_INDEX_V1", "AI_KB_LOCK_V1", "AI_KB_SOURCE_ADMISSION_V1"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_source_time_fields_are_documented_in_all_layers(self):
        text = combined()
        for field in ("source_revisions", "source_edit_times", "source_edit_time_parts"):
            with self.subTest(field=field):
                self.assertIn(field, text)

    def test_content_authority_and_needs_review_are_present(self):
        text = combined()
        self.assertIn("候选正文是页面内容的权威来源", text)
        self.assertIn("needs_review", text)

    def test_admission_and_subtree_rules_are_present(self):
        text = combined()
        self.assertIn("未裁定", text)
        self.assertIn("子树", text)
        self.assertIn("exclude", text)

    def test_local_state_is_not_documented_as_shared_authority(self):
        ingest = INGEST_SKILL.read_text(encoding="utf-8")
        self.assertNotIn("delta_state.json", ingest)

    def test_removed_queue_terms_are_not_current_contract(self):
        text = STORE_SKILL.read_text(encoding="utf-8")
        for term in ("人工编辑优先", "三方合并", "queued"):
            with self.subTest(term=term):
                self.assertNotIn(term, text)

    def test_operator_sequence_matches_store_cli_commands(self):
        text = combined()
        for command in ("source-baseline", "check_delta.py", "generate_entries.py",
                        "prepare", "publish", "verify"):
            with self.subTest(command=command):
                self.assertIn(command, text)

    def test_templates_require_source_edit_time_parts(self):
        templates = TEMPLATES.read_text(encoding="utf-8")
        self.assertIn("source_edit_time_parts", templates)
        self.assertIn("必须长度一致", templates)

    def test_needs_review_is_neutral_and_retryable(self):
        store = STORE_SKILL.read_text(encoding="utf-8")
        self.assertIn("下一轮自然重试", store)

    def test_source_baseline_and_admission_are_named_in_ingest_skill(self):
        ingest = INGEST_SKILL.read_text(encoding="utf-8")
        self.assertIn("source-baseline", ingest)
        self.assertIn("AI_KB_SOURCE_ADMISSION_V1", ingest)

    def test_readonly_acceptance_record_has_required_evidence_fields(self):
        text = READONLY.read_text(encoding="utf-8")
        for field in ("timestamp", "command", "control revision", "sample key",
                      "decision", "schema result", "unresolved items"):
            with self.subTest(field=field):
                self.assertIn(field, text)
        self.assertIn("不得保存令牌", text)

    def test_release_gate_distinguishes_readonly_from_publish(self):
        text = RELEASE_GATE.read_text(encoding="utf-8")
        for phrase in ("第一批", "第二批", "第三批", "read-only",
                       "publish", "explicit user authorization"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()