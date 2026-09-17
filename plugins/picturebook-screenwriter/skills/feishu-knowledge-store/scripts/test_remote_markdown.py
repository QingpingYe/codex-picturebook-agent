import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from remote_markdown import MarkdownNormalizeError, normalize_remote_markdown


class RemoteMarkdownTests(unittest.TestCase):
    def test_control_format_is_normalized(self):
        content = "<title>同步锁</title>\n\n# AI_KB_LOCK_V1\n\n```json\n{}\n```"
        result = normalize_remote_markdown(content, kind="control")
        self.assertTrue(result.startswith("# AI_KB_LOCK_V1\n```json\n"))
        self.assertTrue(result.endswith("```\n"))

    def test_page_format_is_normalized(self):
        content = (
            "<title>世界观</title>\n\n# 世界观\n\n正文。\n\n"
            "## 系统元数据（请勿编辑）\n\n```json\n{}\n```"
        )
        result = normalize_remote_markdown(content, kind="page")
        self.assertTrue(result.startswith("# 世界观\n\n"))
        self.assertTrue(result.endswith("```\n"))

    def test_page_normalization_preserves_internal_blank_lines(self):
        content = (
            "<title>世界观</title>\n\n# 世界观\n\n第一段。\n\n\n\n第二段。\n\n"
            "## 系统元数据（请勿编辑）\n\n```json\n{}\n```"
        )
        result = normalize_remote_markdown(content, kind="page")
        self.assertIn("第一段。\n\n\n\n第二段。", result)

    def test_empty_control_document_is_rejected(self):
        with self.assertRaises(MarkdownNormalizeError):
            normalize_remote_markdown("", kind="control")

    def test_empty_page_document_is_rejected(self):
        with self.assertRaises(MarkdownNormalizeError):
            normalize_remote_markdown("", kind="page")


if __name__ == "__main__":
    unittest.main()
