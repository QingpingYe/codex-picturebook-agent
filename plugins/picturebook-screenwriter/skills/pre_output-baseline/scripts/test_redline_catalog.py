import unittest

from redline_catalog import (
    MACHINE_DATA_BLOCK,
    RedlineRule,
    catalog_from_bundle,
    parse_machine_data_terms,
    parse_quoted_terms,
    rule_triples,
)

CORRECTIONS_KEY = "海外绘本/小老鼠迈尔斯/corrections"

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

WITHOUT_BLOCK = """# 纠正台账

## 强制性禁止条目

绝不可出现 `变勇敢了`、`魔法解决一切` 以及 "tip-tap-tremble" 这类写法。
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

    def test_quoted_and_bare_list_items_both_parse(self):
        terms = parse_machine_data_terms(WITH_BLOCK, MACHINE_DATA_BLOCK)
        self.assertIn("变勇敢了", terms)
        self.assertIn("魔法解决一切", terms)

    def test_a_missing_block_returns_nothing(self):
        self.assertEqual(parse_machine_data_terms(WITHOUT_BLOCK, MACHINE_DATA_BLOCK), ())

    def test_another_block_name_does_not_leak_in(self):
        self.assertEqual(parse_machine_data_terms(WITH_BLOCK, "props"), ())

    def test_the_anchor_line_is_not_read_as_a_term(self):
        self.assertNotIn("redline_terms", parse_machine_data_terms(WITH_BLOCK, MACHINE_DATA_BLOCK))


class ParseQuotedTests(unittest.TestCase):
    def test_backticked_and_quoted_fragments_are_extracted(self):
        terms = parse_quoted_terms('绝不可出现 `变勇敢了` 以及 "魔法解决一切"。')
        self.assertEqual(set(terms), {"变勇敢了", "魔法解决一切"})

    def test_prose_without_markers_yields_nothing(self):
        self.assertEqual(parse_quoted_terms("这一节讲的是为什么这些写法不行。"), ())


class CatalogTests(unittest.TestCase):
    def test_the_block_wins_when_it_is_present(self):
        rules = catalog_from_bundle(bundle(item()))
        self.assertEqual(
            [rule.pattern for rule in rules],
            ["tip-tap-tremble", "变勇敢了", "魔法解决一切"],
        )

    def test_the_prohibition_sections_are_a_fallback_when_the_block_is_absent(self):
        rules = catalog_from_bundle(bundle(item(body=WITHOUT_BLOCK)))
        self.assertEqual(set(rule.pattern for rule in rules),
                         {"变勇敢了", "魔法解决一切", "tip-tap-tremble"})

    def test_the_block_is_preferred_over_the_fallback_for_the_same_page(self):
        rules = catalog_from_bundle(bundle(item()))
        self.assertNotIn("绝不可把解决问题的方式写成", " ".join(r.pattern for r in rules))

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

    def test_the_page_type_decides_whether_a_machine_block_counts(self):
        # The fixture above carries a `redline_terms` block on a page type the
        # templates never declare prohibitions on, so the block is a gap in the
        # knowledge base rather than a red line. Same body, two page types.
        inside = catalog_from_bundle(bundle(item()))
        outside = catalog_from_bundle(bundle(item(key="海外绘本/小老鼠迈尔斯/characters")))
        self.assertEqual(len(inside), 3)
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


class ProhibitionVocabularyAuthorityTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
