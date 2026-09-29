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

class ComparatorContractTests(unittest.TestCase):
    def test_mark_insensitive_semantics(self):
        from remote_markdown import mark_insensitive
        self.assertEqual(mark_insensitive('# **Hello**\n> _world_\n[https://x](https://x)\n'), 'Hello\nworld\nhttps://x')
        self.assertEqual(mark_insensitive('a_b_c https://x/a_b_c `*code*` a * b * c'), 'a_b_c https://x/a_b_c `*code*` a * b * c')
        self.assertEqual(mark_insensitive('a *x*'), 'a x')



class MarkInsensitiveContractTests(unittest.TestCase):
    def test_normalizes_markdown_presentation(self):
        from remote_markdown import mark_insensitive
        self.assertEqual(mark_insensitive("**bold** and _emphasis_"), "bold and emphasis")
        self.assertEqual(mark_insensitive("# Heading\n> > quoted\ntext  "), "Heading\nquoted\ntext")
        self.assertEqual(mark_insensitive("[https://x/a](https://x/a)"), "https://x/a")
        self.assertEqual(mark_insensitive("| a | b |\n|---|---|\n| x | y |"), "| a | b\n|---|---\n| x | y")

    def test_preserves_literal_marks_links_code_and_punctuation(self):
        from remote_markdown import mark_insensitive
        samples = [
            "a_b_c", "https://x/a_b_c", "https://x/*abc*/", "[t](https://x/*abc*/) ",
            "[label](https://x/a_b_c)", "`*code* _span_`",
            "```md\n*code* _span_\n```", "URL text https://x/*abc*/",
            "*unpaired", "_unpaired", "a * b * c", "literal |", "a >x", "changed!",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(mark_insensitive(sample), sample.rstrip())

    def test_changed_targets_and_punctuation_remain_significant(self):
        from remote_markdown import mark_insensitive
        self.assertNotEqual(mark_insensitive("[label](https://x/a)"), mark_insensitive("[label](https://x/b)"))
        self.assertNotEqual(mark_insensitive("word."), mark_insensitive("word!"))


class ComparatorRoundThreeTests(unittest.TestCase):
    def test_asterisks_inside_words_are_literal(self):
        from remote_markdown import mark_insensitive
        self.assertNotEqual(mark_insensitive("1*2*3"), mark_insensitive("123"))
        self.assertNotEqual(mark_insensitive("a*b*c"), mark_insensitive("abc"))

    def test_longer_fence_closers_preserve_code_markers(self):
        from remote_markdown import mark_insensitive
        for opening, closing in (("```", "````"), ("~~~", "~~~~")):
            code = f"{opening}md\n*x*\n{closing}"
            self.assertEqual(mark_insensitive(code), code)

    def test_emphasis_around_url_is_ignored(self):
        from remote_markdown import mark_insensitive
        self.assertEqual(mark_insensitive("**https://x/a**"), mark_insensitive("https://x/a"))

class ComparatorUrlPunctuationTests(unittest.TestCase):
    def test_url_emphasis_before_sentence_punctuation_is_ignored(self):
        from remote_markdown import mark_insensitive
        for marker in ('*', '**', '_', '__'):
            for punctuation in ('.', ',', '!', '?', ';', ':', '...', '。', '，', '！', '？'):
                with self.subTest(marker=marker, punctuation=punctuation):
                    plain = 'https://x/a' + punctuation
                    self.assertEqual(mark_insensitive(marker + 'https://x/a' + marker + punctuation), plain)

    def test_url_literal_marks_and_targets_before_punctuation_are_preserved(self):
        from remote_markdown import mark_insensitive
        samples = (
            'https://x/a*b*.', 'https://x/a_b_.', 'https://x/a**.',
            'https://x/a__.', 'https://x/*a*/.', 'https://x/a*b*c,',
            '[label](https://x/a**.),', '[https://x/a*.](https://x/a*.)',
            '`**https://x/a**.`', '```md\n**https://x/a**.\n```',
        )
        for sample in samples:
            with self.subTest(sample=sample):
                expected = 'https://x/a*.' if sample.startswith('[https:') else sample
                self.assertEqual(mark_insensitive(sample), expected)
        self.assertNotEqual(mark_insensitive('**https://x/a**.'), mark_insensitive('https://x/a,'))
        self.assertNotEqual(mark_insensitive('[label](https://x/a**.)'), mark_insensitive('[label](https://x/a.)'))
