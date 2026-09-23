import unittest

from chunker import Chunk, chunk_body, chunk_bundle, chunk_evidence

WORLDVIEW_BODY = """# 世界观总纲

一句话概括：小小探险家迈尔斯在每一次冒险中学习与伙伴相处。

## 核心价值主张

勇气不是不害怕，而是害怕时仍然向前。

## 创作红线不变量

- 迈尔斯不能飞行。
- 不能用魔法解决冲突。

## 场景清单

| 场景 | 说明 |
| --- | --- |
| 森林 | 迈尔斯家附近 |
"""

CORRECTIONS_BODY = """# 纠正台账

## 强制性禁止条目

绝不可出现的写法：把解决问题的方式写成"变勇敢了"。

## 红线机器可读块

<!-- machine-data: redline_terms -->
```yaml
redline_terms:
  - "tip-tap-tremble"
  - "变勇敢了"
```

## 迭代历史

- 2026-09-01：新增一条禁止词。
"""

FENCED_HASH_BODY = """# 台账

## 数据块

<!-- machine-data: props -->
```yaml
# 这是 YAML 注释，不是标题
props:
  - name: "指南针"
```
"""


def evidence(key="海外绘本/小老鼠迈尔斯/worldview", body=WORLDVIEW_BODY, **overrides):
    item = {
        "key": key,
        "doc_token": "doxcnExample",
        "revision_id": 17,
        "title": key,
        "content": body,
        "source_revisions": {"node-a": "17"},
        "status": "published",
        "index_synced": True,
    }
    item.update(overrides)
    return item


class ChunkBodyTests(unittest.TestCase):
    def test_a_heading_opens_a_section_carrying_its_ancestors(self):
        sections = chunk_body(WORLDVIEW_BODY)
        paths = [tuple(section["heading_path"]) for section in sections]
        self.assertIn(("世界观总纲",), paths)
        self.assertIn(("世界观总纲", "创作红线不变量"), paths)
        self.assertIn(("世界观总纲", "场景清单"), paths)

    def test_a_heading_line_is_kept_inside_its_own_section(self):
        sections = {tuple(s["heading_path"]): s["text"] for s in chunk_body(WORLDVIEW_BODY)}
        self.assertTrue(sections[("世界观总纲", "创作红线不变量")]
                        .startswith("## 创作红线不变量"))

    def test_markdown_tables_stay_inside_their_section(self):
        sections = {tuple(s["heading_path"]): s["text"] for s in chunk_body(WORLDVIEW_BODY)}
        self.assertIn("| 森林 |", sections[("世界观总纲", "场景清单")])

    def test_a_hash_inside_a_fence_is_not_a_heading(self):
        paths = [tuple(section["heading_path"]) for section in chunk_body(FENCED_HASH_BODY)]
        self.assertEqual(paths, [("台账",), ("台账", "数据块")])
        self.assertNotIn("这是 YAML 注释", " / ".join(title for path in paths for title in path))

    def test_a_fenced_block_stays_whole_inside_one_section(self):
        sections = {tuple(s["heading_path"]): s["text"] for s in chunk_body(FENCED_HASH_BODY)}
        text = sections[("台账", "数据块")]
        self.assertIn("```yaml", text)
        self.assertIn('name: "指南针"', text)
        self.assertIn("```", text)

    def test_a_preamble_before_any_heading_becomes_its_own_section(self):
        sections = chunk_body("开头没有标题的一段话。\n\n# 第一章\n\n正文。\n")
        self.assertEqual(tuple(sections[0]["heading_path"]), ())
        self.assertEqual(sections[0]["text"], "开头没有标题的一段话。")

    def test_a_long_section_is_split_at_paragraph_boundaries(self):
        paragraphs = "\n\n".join(f"第 {index} 段。" + "字" * 60 for index in range(40))
        sections = chunk_body(f"# 长章节\n\n{paragraphs}\n")
        self.assertGreater(len(sections), 1)
        for section in sections:
            self.assertLessEqual(len(section["text"]), 1600 + 200)

    def test_an_unbreakable_paragraph_is_split_at_the_cap(self):
        # A wall of text with no blank line can still be longer than the
        # budget: one oversized chunk would push the whole request past the
        # model's context limit, so the cut has to happen inside the paragraph.
        sections = chunk_body("# 长章节\n\n" + "字" * 5000 + "\n")
        self.assertGreater(len(sections), 1)
        for section in sections:
            self.assertLessEqual(len(section["text"]), 1600)

    def test_an_unterminated_fence_does_not_hide_later_headings(self):
        # Pages are hand-edited and lose their closing fence easily. Treating
        # an orphan opener as a fence would swallow every later heading, and
        # the hard-constraint sections after it would silently lose the
        # required mark they depend on.
        body = (
            "# 世界观总纲\n\n```\n未收尾的围栏\n\n"
            "## 创作红线不变量\n\n- 迈尔斯不能飞行。\n"
        )
        sections = chunk_body(body)
        self.assertEqual(
            [tuple(section["heading_path"]) for section in sections],
            [("世界观总纲",), ("世界观总纲", "创作红线不变量")],
        )
        self.assertIn("迈尔斯不能飞行", sections[1]["text"])

    def test_blank_body_produces_no_sections(self):
        self.assertEqual(chunk_body("\n\n   \n"), [])


class ChunkEvidenceTests(unittest.TestCase):
    def test_chunk_ids_are_stable_and_carry_the_page_key(self):
        chunks = chunk_evidence(evidence())
        self.assertEqual(chunks[0].chunk_id, "海外绘本/小老鼠迈尔斯/worldview#000")
        self.assertEqual(chunks[1].chunk_id, "海外绘本/小老鼠迈尔斯/worldview#001")

    def test_every_chunk_carries_the_page_version_vector(self):
        for chunk in chunk_evidence(evidence()):
            with self.subTest(chunk=chunk.chunk_id):
                self.assertEqual(chunk.doc_token, "doxcnExample")
                self.assertEqual(chunk.revision_id, 17)
                self.assertEqual(chunk.key, "海外绘本/小老鼠迈尔斯/worldview")

    def test_chunks_start_unmarked(self):
        for chunk in chunk_evidence(evidence()):
            self.assertFalse(chunk.required)
            self.assertIsNone(chunk.required_reason)

    def test_chunk_round_trips_through_json_safe_types(self):
        chunk = chunk_evidence(evidence())[0]
        payload = chunk.to_dict()
        self.assertEqual(payload["heading_path"], ["世界观总纲"])
        self.assertIsInstance(payload["chunk_id"], str)

    def test_chunk_bundle_accepts_dataclass_and_mapping_items(self):
        class Item:
            key = "海外绘本/小老鼠迈尔斯/worldview"
            doc_token = "doxcnExample"
            revision_id = 17
            title = key
            content = WORLDVIEW_BODY
            source_revisions = {"node-a": "17"}
            status = "published"
            index_synced = True

        from_dataclass = chunk_bundle({"items": (Item(),)})
        from_mapping = chunk_bundle({"items": (evidence(),)})
        self.assertEqual([c.chunk_id for c in from_dataclass],
                         [c.chunk_id for c in from_mapping])

    def test_a_page_and_a_mapping_produce_the_same_chunks(self):
        self.assertEqual(
            [c.to_dict() for c in chunk_evidence(evidence())],
            [c.to_dict() for c in chunk_evidence(evidence())],
        )


if __name__ == "__main__":
    unittest.main()
