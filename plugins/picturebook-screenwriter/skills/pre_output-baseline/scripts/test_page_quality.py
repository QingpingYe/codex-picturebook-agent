import unittest

from page_quality import (
    DIMENSIONS,
    LAST_PAGE_EXEMPT_DIMENSIONS,
    dimensions_for,
    is_last_page,
    last_page_no,
    page_facts,
    parse_script_pages,
)

SCRIPT = """# 学会分享

| # | 页码 | Text | 插图 |
| --- | --- | --- | --- |
| 1 | 1 | Miles found a red ball. / He held it tight. | 男孩抱着红球 |
| 2 | 2 | "Mine!" he said. | 男孩转身 |
| 3 | 3 | Then he saw Mia sitting alone. | 女孩独自坐着 |
| 4 | 4 | Miles rolled the ball to her. / "Let's play!" | 两人一起玩 |
"""


class ParseTests(unittest.TestCase):
    def test_pages_come_from_the_existing_script_parser(self):
        pages = parse_script_pages(SCRIPT)
        self.assertEqual([page["no"] for page in pages], ["1", "2", "3", "4"])

    def test_page_text_is_carried_through(self):
        pages = parse_script_pages(SCRIPT)
        self.assertIn("Miles found a red ball.", pages[0]["text"])


class LastPageTests(unittest.TestCase):
    def test_the_last_page_is_the_highest_numbered_one(self):
        self.assertEqual(last_page_no(parse_script_pages(SCRIPT)), "4")

    def test_no_pages_means_no_last_page(self):
        self.assertEqual(last_page_no(()), "")

    def test_the_tenth_page_is_the_last_page_not_the_ninth(self):
        # Picture books run 24-32 pages. Comparing page numbers as strings
        # would put "10" before "9" and mark page 9 as the closing page.
        rows = "\n".join(
            f"| {index} | {index} | Page {index} text. / Second line. | 插图 {index} |"
            for index in range(1, 11)
        )
        script = (
            "# 十页草稿\n\n| # | 页码 | Text | 插图 |\n| --- | --- | --- | --- |\n"
            + rows + "\n"
        )
        pages = parse_script_pages(script)
        self.assertEqual(last_page_no(pages), "10")
        self.assertTrue(is_last_page(pages, "10"))
        self.assertFalse(is_last_page(pages, "9"))

    def test_a_non_numeric_page_number_is_not_the_last_page(self):
        # Front matter carries page numbers like 封面 or 自述. Counting one of
        # them as the final page would drop the page-turn dimension from the
        # real closing page instead.
        #
        # parse_script reads the page number from the first page-number-ish
        # column, so 封面 has to sit in that column: a table that also carries
        # a numeric index column ahead of it never yields a non-numeric "no".
        rows = "\n".join([
            "| 封面 | 无字页，只画一棵树。 | 树 |",
            "| 1 | Miles found a red ball. | 红球 |",
            "| 2 | Miles rolled it to Mia. | 两人 |",
        ])
        script = (
            "# 草稿\n\n| # | Text | 插图 |\n| --- | --- | --- |\n"
            + rows + "\n"
        )
        pages = parse_script_pages(script)
        self.assertEqual([page["no"] for page in pages], ["封面", "1", "2"])
        self.assertEqual(last_page_no(pages), "2")
        self.assertFalse(is_last_page(pages, "封面"))


class FactTests(unittest.TestCase):
    def test_character_count_ignores_the_line_separator(self):
        facts = page_facts("Miles rolled. / Let's play!")
        self.assertEqual(facts["char_count"], len("Miles rolled. Let's play!"))

    def test_sentence_count_splits_on_terminators(self):
        facts = page_facts("Mine! He said. What now?")
        self.assertEqual(facts["sentence_count"], 3)

    def test_max_line_repeat_reports_a_refrain(self):
        facts = page_facts("Tip tap tremble. / Something else. / Tip tap tremble.")
        self.assertEqual(facts["max_line_repeat"], 2)

    def test_a_page_without_repetition_reports_one(self):
        facts = page_facts("Miles ran. / Mia laughed.")
        self.assertEqual(facts["max_line_repeat"], 1)

    def test_empty_text_reports_zeroes_not_an_error(self):
        facts = page_facts("")
        self.assertEqual(facts["char_count"], 0)
        self.assertEqual(facts["sentence_count"], 0)
        self.assertEqual(facts["max_line_repeat"], 0)

    def test_facts_are_plain_json_safe_values(self):
        for value in page_facts("Miles ran.").values():
            self.assertIsInstance(value, int)


class DimensionsForTests(unittest.TestCase):
    def test_a_non_final_page_gets_every_dimension(self):
        self.assertEqual(dimensions_for("1", is_last_page=False), DIMENSIONS)

    def test_the_final_page_drops_the_page_turn_dimension(self):
        self.assertEqual(
            dimensions_for("4", is_last_page=True),
            tuple(d for d in DIMENSIONS if d != "weak_page_turn_motivation"),
        )

    def test_the_final_page_keeps_every_other_dimension(self):
        kept = dimensions_for("4", is_last_page=True)
        self.assertIn("direct_moralizing", kept)
        self.assertIn("emotion_told_not_shown", kept)
        self.assertEqual(len(kept), len(DIMENSIONS) - 1)

    def test_no_other_dimension_is_exempt(self):
        self.assertEqual(LAST_PAGE_EXEMPT_DIMENSIONS, ("weak_page_turn_motivation",))

    def test_the_five_dimensions_are_the_ones_the_spec_names(self):
        self.assertEqual(DIMENSIONS, (
            "direct_moralizing", "age_comprehension_risk", "read_aloud_friction",
            "weak_page_turn_motivation", "emotion_told_not_shown",
        ))


class LastPagePredicateTests(unittest.TestCase):
    def test_only_the_highest_numbered_page_is_last(self):
        pages = parse_script_pages(SCRIPT)
        self.assertTrue(is_last_page(pages, "4"))
        self.assertFalse(is_last_page(pages, "3"))

    def test_no_pages_makes_nothing_the_last_page(self):
        self.assertFalse(is_last_page((), "4"))


if __name__ == "__main__":
    unittest.main()
