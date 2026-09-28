import unittest

from redline_catalog import (
    MACHINE_DATA_BLOCK,
    RedlineRule,
    catalog_from_bundle,
    machine_block_terms,
    parse_machine_data_terms,
    parse_quoted_terms,
    rule_triples,
)

CORRECTIONS_KEY = "海外绘本/小老鼠迈尔斯/corrections"
CHARACTERS_KEY = "海外绘本/小老鼠迈尔斯/characters"
FINGERPRINT_KEY = "海外绘本/小老鼠迈尔斯/story-fingerprint-spec"

WITH_BLOCK = """# 纠正台账

## 强制性禁止条目

绝不可把解决问题的方式写成"变勇敢了"。

## 红线机器可读块

<!-- machine-data: redline_terms -->
```yaml
redline_terms:
  - "tip-tap-tremble"
  - "变勇敢了"
  - 魔法解决一切
```

## 迭代历史

- 2026-09-01：新增一条。
"""

# The shape structured-output-templates.md prints for corrections: the anchor is
# followed by the bare key line and its list, with no fence around it.
UNFENCED_BLOCK = """# 纠正台账

## 红线机器可读块

<!-- machine-data: redline_terms -->
redline_terms:
  - "tip-tap-tremble"
  - "变勇敢了"
  - 魔法解决一切

## 迭代历史

- 2026-09-01：新增一条。
"""

WITHOUT_BLOCK = """# 纠正台账

## 强制性禁止条目

绝不可出现 `变勇敢了`、`魔法解决一切` 以及 "tip-tap-tremble" 这类写法。
"""

# The anchor and the prose quotes sit in one section, so the block has to win
# inside that section rather than only across sections.
SAME_SECTION_BLOCK = """# 纠正台账

## 强制性禁止条目

绝不可把解决问题的方式写成 `变勇敢了`。

<!-- machine-data: redline_terms -->
redline_terms:
  - "tip-tap-tremble"
"""

# The fence belongs to a later section of the same page, not to the anchor above.
LATER_UNRELATED_FENCE = """# 纠正台账

## 红线机器可读块

<!-- machine-data: redline_terms -->

```yaml
props:
  - "无关内容"
```
"""

# An orphan opener is text, not a block: `chunker` already refuses to open a
# fence that nothing closes.
UNTERMINATED_FENCE_BLOCK = """# 纠正台账

## 红线机器可读块

<!-- machine-data: redline_terms -->
```yaml
redline_terms:
  - "不该读到的词"
"""

BOTH_MACHINE_BLOCKS = """# 纠正台账

## 强制性禁止条目

<!-- machine-data: redline_terms -->
```yaml
redline_terms:
  - "变勇敢了"
```

<!-- machine-data: banned_terms -->
```yaml
banned_terms:
  - "小英雄"
```
"""

BANNED_TERMS_BLOCK = """# 选题指纹与门禁规格

## 禁用词与禁止写法

<!-- machine-data: banned_terms -->
```yaml
banned_terms:
  - "小英雄"
  - "突然之间"
```
"""

# 「创作边界」 is declared a constraint heading for this page type even though it
# carries none of the generic prohibition words.
CHARACTERS_BODY = """# 角色档案

## 创作边界

迈尔斯绝不可说出 `随它去吧` 这类话。
"""

# 「金句纠正」 is a corrections constraint heading with no generic marker either.
CORRECTIONS_GOLDEN_BODY = """# 纠正台账

## 金句纠正

不要写 `我长大了` 这种句式。
"""

# A constraint section that carries a fence but no machine block: the fallback
# has to read the quoted fragment and nothing else.
FENCED_PROSE_SECTION = """# 纠正台账

## 强制性禁止条目

绝不可出现下面这类写法：

```yaml
props:
  - "无关内容"
```
"""


def item(key=CORRECTIONS_KEY, body=WITH_BLOCK, revision_id=17):
    return {
        "key": key,
        "doc_token": "doxcnExample",
        "revision_id": revision_id,
        "title": key,
        "content": body,
        "source_revisions": {"node-a": str(revision_id)},
        "status": "published",
        "index_synced": True,
    }


def bundle(*items):
    return {"items": items, "warnings": (), "offline": False,
            "fetched_at": "2026-09-23T10:30:00+08:00"}


class ParseMachineDataTests(unittest.TestCase):
    def test_terms_are_read_from_the_fenced_block(self):
        self.assertEqual(
            parse_machine_data_terms(WITH_BLOCK, MACHINE_DATA_BLOCK),
            ("tip-tap-tremble", "变勇敢了", "魔法解决一切"),
        )

    def test_the_unfenced_template_shape_is_read_too(self):
        self.assertEqual(
            parse_machine_data_terms(UNFENCED_BLOCK, MACHINE_DATA_BLOCK),
            ("tip-tap-tremble", "变勇敢了", "魔法解决一切"),
        )

    def test_quoted_and_bare_list_items_both_parse(self):
        terms = parse_machine_data_terms(WITH_BLOCK, MACHINE_DATA_BLOCK)
        self.assertIn("变勇敢了", terms)
        self.assertIn("魔法解决一切", terms)

    def test_a_fence_that_does_not_open_right_after_the_anchor_is_not_this_block(self):
        self.assertEqual(parse_machine_data_terms(LATER_UNRELATED_FENCE, MACHINE_DATA_BLOCK), ())

    def test_an_unclosed_fence_is_not_read_as_the_block(self):
        self.assertEqual(
            parse_machine_data_terms(UNTERMINATED_FENCE_BLOCK, MACHINE_DATA_BLOCK), ()
        )

    def test_a_differently_cased_or_spaced_anchor_is_read(self):
        cased = UNFENCED_BLOCK.replace(
            "<!-- machine-data: redline_terms -->", "<!-- Machine-Data:redline_terms-->"
        )
        self.assertEqual(
            parse_machine_data_terms(cased, MACHINE_DATA_BLOCK),
            ("tip-tap-tremble", "变勇敢了", "魔法解决一切"),
        )

    def test_a_missing_block_returns_nothing(self):
        self.assertEqual(parse_machine_data_terms(WITHOUT_BLOCK, MACHINE_DATA_BLOCK), ())

    def test_another_block_name_does_not_leak_in(self):
        self.assertEqual(parse_machine_data_terms(WITH_BLOCK, "props"), ())

    def test_the_anchor_line_is_not_read_as_a_term(self):
        self.assertNotIn("redline_terms", parse_machine_data_terms(WITH_BLOCK, MACHINE_DATA_BLOCK))


class MachineBlockTermsTests(unittest.TestCase):
    def test_both_declared_machine_blocks_are_read(self):
        self.assertEqual(machine_block_terms(BOTH_MACHINE_BLOCKS), ("变勇敢了", "小英雄"))


class ParseQuotedTests(unittest.TestCase):
    def test_backticked_and_quoted_fragments_are_extracted(self):
        terms = parse_quoted_terms('绝不可出现 `变勇敢了` 以及 "魔法解决一切"。')
        self.assertEqual(set(terms), {"变勇敢了", "魔法解决一切"})

    def test_a_fence_delimiter_is_not_a_quoted_fragment(self):
        terms = parse_quoted_terms('```yaml\nprops:\n  - "无关内容"\n```')
        self.assertEqual(terms, ("无关内容",))

    def test_a_fragment_never_spans_a_line(self):
        self.assertEqual(parse_quoted_terms("绝不可写 `跨行\n内容`。"), ())

    def test_prose_without_markers_yields_nothing(self):
        self.assertEqual(parse_quoted_terms("这一节讲的是为什么这些写法不行。"), ())


class CatalogTests(unittest.TestCase):
    def test_the_block_wins_when_it_is_present(self):
        rules = catalog_from_bundle(bundle(item()))
        self.assertEqual(
            [rule.pattern for rule in rules],
            ["tip-tap-tremble", "变勇敢了", "魔法解决一切"],
        )

    def test_the_unfenced_template_shape_reaches_the_catalog(self):
        rules = catalog_from_bundle(bundle(item(body=UNFENCED_BLOCK)))
        self.assertEqual(
            [rule.pattern for rule in rules],
            ["tip-tap-tremble", "变勇敢了", "魔法解决一切"],
        )

    def test_the_prohibition_sections_are_a_fallback_when_the_block_is_absent(self):
        rules = catalog_from_bundle(bundle(item(body=WITHOUT_BLOCK)))
        self.assertEqual(set(rule.pattern for rule in rules),
                         {"变勇敢了", "魔法解决一切", "tip-tap-tremble"})

    def test_a_fenced_section_is_scraped_without_its_fence_delimiters(self):
        rules = catalog_from_bundle(bundle(item(body=FENCED_PROSE_SECTION)))
        self.assertEqual([rule.pattern for rule in rules], ["无关内容"])

    def test_the_block_is_preferred_over_the_fallback_for_the_same_section(self):
        rules = catalog_from_bundle(bundle(item(body=SAME_SECTION_BLOCK)))
        self.assertEqual([rule.pattern for rule in rules], ["tip-tap-tremble"])

    def test_a_declared_constraint_heading_is_scraped_even_without_a_generic_marker(self):
        characters = catalog_from_bundle(bundle(item(key=CHARACTERS_KEY, body=CHARACTERS_BODY)))
        golden = catalog_from_bundle(bundle(item(body=CORRECTIONS_GOLDEN_BODY)))
        self.assertEqual([rule.pattern for rule in characters], ["随它去吧"])
        self.assertEqual([rule.pattern for rule in golden], ["我长大了"])

    def test_the_banned_terms_block_feeds_the_catalog(self):
        rules = catalog_from_bundle(bundle(item(key=FINGERPRINT_KEY, body=BANNED_TERMS_BLOCK)))
        self.assertEqual([rule.pattern for rule in rules], ["小英雄", "突然之间"])

    def test_both_machine_blocks_of_one_page_are_catalogued(self):
        rules = catalog_from_bundle(bundle(item(body=BOTH_MACHINE_BLOCKS)))
        self.assertEqual([rule.pattern for rule in rules], ["变勇敢了", "小英雄"])

    def test_every_rule_carries_its_source_page_and_revision(self):
        for rule in catalog_from_bundle(bundle(item())):
            with self.subTest(pattern=rule.pattern):
                self.assertEqual(rule.source_key, CORRECTIONS_KEY)
                self.assertEqual(rule.revision_id, 17)

    def test_rule_ids_are_stable_for_the_same_pattern(self):
        first = {rule.pattern: rule.rule_id for rule in catalog_from_bundle(bundle(item()))}
        second = {rule.pattern: rule.rule_id
                  for rule in catalog_from_bundle(bundle(item(revision_id=18)))}
        self.assertEqual(first, second)

    def test_a_pattern_appearing_on_two_pages_is_deduplicated(self):
        other = item(key="海外绘本/小老鼠迈尔斯/creation-standards")
        rules = catalog_from_bundle(bundle(item(), other))
        patterns = [rule.pattern for rule in rules]
        self.assertEqual(len(patterns), len(set(patterns)))

    def test_a_page_with_no_prohibitions_contributes_nothing(self):
        rules = catalog_from_bundle(bundle(item(key="海外绘本/小老鼠迈尔斯/ip-overview")))
        self.assertEqual(rules, ())

    def test_a_machine_block_counts_on_every_constraint_page(self):
        # Same body, two page types: `characters` is in scope because it declares
        # its own constraint heading, `ip-overview` declares none.
        inside = catalog_from_bundle(bundle(item(key=CHARACTERS_KEY)))
        outside = catalog_from_bundle(bundle(item(key="海外绘本/小老鼠迈尔斯/ip-overview")))
        self.assertEqual(
            [rule.pattern for rule in inside],
            ["tip-tap-tremble", "变勇敢了", "魔法解决一切"],
        )
        self.assertEqual(outside, ())

    def test_an_empty_bundle_yields_an_empty_catalog(self):
        self.assertEqual(catalog_from_bundle(bundle()), ())

    def test_rule_triples_match_the_proxy_scanner_signature(self):
        triples = rule_triples(catalog_from_bundle(bundle(item())))
        self.assertEqual(len(triples[0]), 3)
        self.assertTrue(all(isinstance(part, str) for part in triples[0]))

    def test_a_rule_describes_itself(self):
        rule = RedlineRule("redline-abc", "变勇敢了", "禁止直接把成长写成变勇敢", CORRECTIONS_KEY, 17)
        self.assertEqual(rule.as_triple(),
                         ("redline-abc", "变勇敢了", "禁止直接把成长写成变勇敢"))


class ConstraintVocabularyAuthorityTests(unittest.TestCase):
    def test_the_prohibition_vocabulary_has_a_single_authority(self):
        # The marking stage and the catalog stage must share one vocabulary, or
        # a section could be protected by one and filtered by the other.
        import redline_catalog
        from required_marking import (
            PROHIBITION_HEADING_MARKERS as AUTHORITY_MARKERS,
            PROHIBITION_PAGE_TYPES as AUTHORITY_TYPES,
        )

        self.assertIs(redline_catalog.PROHIBITION_PAGE_TYPES, AUTHORITY_TYPES)
        self.assertIs(redline_catalog.PROHIBITION_HEADING_MARKERS, AUTHORITY_MARKERS)

    def test_the_constraint_pages_cover_every_declared_constraint_heading(self):
        import redline_catalog
        from required_marking import REQUIRED_HEADING_MARKERS as AUTHORITY_HEADINGS

        self.assertEqual(
            set(redline_catalog.CONSTRAINT_PAGE_TYPES),
            set(redline_catalog.PROHIBITION_PAGE_TYPES) | set(AUTHORITY_HEADINGS),
        )


if __name__ == "__main__":
    unittest.main()
