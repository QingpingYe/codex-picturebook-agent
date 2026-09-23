import unittest

import required_marking
from chunker import MAX_CHUNK_CHARS, chunk_evidence
from load_knowledge import KnowledgeEvidence, KnowledgeEvidenceBundle
from required_marking import (
    REQUIRED_ALL_PAGE_TYPES,
    REQUIRED_MACHINE_DATA_BLOCKS,
    mark_bundle,
    mark_required,
    page_type_of,
)

from test_chunker import CORRECTIONS_BODY, FENCED_HASH_BODY, WORLDVIEW_BODY, evidence


def marked(key, body, **overrides):
    item = evidence(key=key, body=body, **overrides)
    chunks = chunk_evidence(item)
    return mark_required(
        chunks,
        page_type=page_type_of(key),
        status=item["status"],
        index_synced=item["index_synced"],
    )


def by_heading(chunks):
    return {" / ".join(chunk.heading_path): chunk for chunk in chunks}


def without(item, name):
    return {key: value for key, value in item.items() if key != name}


class PageTypeTests(unittest.TestCase):
    def test_page_type_is_the_last_key_segment(self):
        self.assertEqual(page_type_of("海外绘本/小老鼠迈尔斯/worldview"), "worldview")
        self.assertEqual(page_type_of("common/common/creation-standards"), "creation-standards")

    def test_a_key_without_three_segments_has_no_page_type(self):
        self.assertEqual(page_type_of("worldview"), "")
        self.assertEqual(page_type_of(""), "")


class RequiredAllTests(unittest.TestCase):
    def test_content_spec_is_required_in_full(self):
        chunks = marked("海外绘本/小老鼠迈尔斯/content-spec", WORLDVIEW_BODY)
        self.assertTrue(all(chunk.required for chunk in chunks))
        self.assertEqual(chunks[0].required_reason, "page_type:content-spec")

    def test_content_spec_is_the_only_whole_page_required_type(self):
        self.assertEqual(REQUIRED_ALL_PAGE_TYPES, frozenset({"content-spec"}))


class RequiredHeadingTests(unittest.TestCase):
    def test_worldview_red_lines_are_required_and_other_sections_are_not(self):
        chunks = by_heading(marked("海外绘本/小老鼠迈尔斯/worldview", WORLDVIEW_BODY))
        self.assertTrue(chunks["世界观总纲 / 创作红线不变量"].required)
        self.assertEqual(chunks["世界观总纲 / 创作红线不变量"].required_reason,
                         "heading:创作红线不变量")
        self.assertFalse(chunks["世界观总纲 / 场景清单"].required)
        self.assertFalse(chunks["世界观总纲 / 核心价值主张"].required)

    def test_corrections_prohibition_sections_are_required(self):
        chunks = by_heading(marked("海外绘本/小老鼠迈尔斯/corrections", CORRECTIONS_BODY))
        self.assertTrue(chunks["纠正台账 / 强制性禁止条目"].required)
        self.assertFalse(chunks["纠正台账 / 迭代历史"].required)

    def test_creation_standards_red_lines_are_required(self):
        body = "# 创作规范\n\n## 创作红线\n\n- 不许出现说教。\n\n## 排版\n\n- 每页不超过三行。\n"
        chunks = by_heading(marked("common/common/creation-standards", body))
        self.assertTrue(chunks["创作规范 / 创作红线"].required)
        self.assertFalse(chunks["创作规范 / 排版"].required)


class RequiredMachineDataTests(unittest.TestCase):
    def test_a_redline_terms_block_is_required(self):
        chunks = by_heading(marked("海外绘本/小老鼠迈尔斯/corrections", CORRECTIONS_BODY))
        self.assertTrue(chunks["纠正台账 / 红线机器可读块"].required)
        self.assertEqual(chunks["纠正台账 / 红线机器可读块"].required_reason,
                         "machine_data:redline_terms")

    def test_machine_data_is_required_even_without_a_matching_heading(self):
        body = "# 台账\n\n## 随便一个标题\n\n<!-- machine-data: props -->\n```yaml\nprops: []\n```\n"
        chunks = marked("海外绘本/小老鼠迈尔斯/prop-registry", body)
        self.assertTrue(chunks[1].required)
        self.assertEqual(chunks[1].required_reason, "machine_data:props")

    def test_every_declared_machine_data_block_is_never_filterable(self):
        for block in REQUIRED_MACHINE_DATA_BLOCKS:
            body = f"# 台账\n\n## 数据\n\n<!-- machine-data: {block} -->\n```yaml\nx: 1\n```\n"
            with self.subTest(block=block):
                chunks = marked("海外绘本/小老鼠迈尔斯/story-fingerprint-spec", body)
                self.assertTrue(chunks[1].required)

    def test_an_unheaded_preamble_is_required(self):
        chunks = marked("海外绘本/小老鼠迈尔斯/worldview", "开头一段没有标题。\n\n# 总纲\n\n正文。\n")
        self.assertTrue(chunks[0].required)
        self.assertEqual(chunks[0].required_reason, "unclassified_preamble")


class MachineDataAnchorTests(unittest.TestCase):
    """The declared block names are a vocabulary, not the boundary."""

    def test_an_unlisted_machine_data_block_is_required(self):
        body = ("# 台账\n\n## 数据\n\n<!-- machine-data: style_notes -->\n"
                "```yaml\nx: 1\n```\n")
        chunks = marked("海外绘本/小老鼠迈尔斯/prop-registry", body)
        self.assertTrue(chunks[1].required)
        self.assertEqual(chunks[1].required_reason, "machine_data:style_notes")

    def test_an_anchor_written_without_spaces_is_required(self):
        # The templates show the spaced form, but a page may write the anchor
        # tight against its delimiters, and that block is still a constraint.
        body = "# 台账\n\n## 数据\n\n<!--machine-data:props-->\n```yaml\nprops: []\n```\n"
        chunks = marked("海外绘本/小老鼠迈尔斯/prop-registry", body)
        self.assertTrue(chunks[1].required)
        self.assertEqual(chunks[1].required_reason, "machine_data:props")

    def test_an_anchor_without_a_name_is_required(self):
        body = "# 台账\n\n## 数据\n\n<!-- machine-data: -->\n```yaml\nx: 1\n```\n"
        chunks = marked("海外绘本/小老鼠迈尔斯/prop-registry", body)
        self.assertTrue(chunks[1].required)
        self.assertEqual(chunks[1].required_reason, "machine_data:unnamed")


class ProhibitionVocabularyTests(unittest.TestCase):
    """Phase 3 reads prohibitions from the headings Phase 2 protects."""

    # The marker vocabulary Phase 3's redline_catalog declares.
    PHASE3_MARKERS = ("禁止", "红线", "禁用", "创作边界", "金句规则")

    def test_the_prohibition_vocabulary_matches_phase3(self):
        self.assertEqual(required_marking.PROHIBITION_HEADING_MARKERS,
                         self.PHASE3_MARKERS)

    def test_a_phase3_prohibition_heading_is_required(self):
        for marker in self.PHASE3_MARKERS:
            body = f"# 世界观总纲\n\n## {marker}清单\n\n- 一条约束。\n"
            with self.subTest(marker=marker):
                chunks = by_heading(marked("海外绘本/小老鼠迈尔斯/worldview", body))
                self.assertTrue(chunks[f"世界观总纲 / {marker}清单"].required)

    def test_the_corrections_sentence_and_frame_sections_are_required(self):
        body = ("# 纠正台账\n\n## 金句纠正\n\n- 被否决的金句模式。\n\n"
                "## 画面纠正\n\n- 被否决的画面处理方式。\n\n"
                "## 迭代历史\n\n- 2026-09-01：新增一条。\n")
        chunks = by_heading(marked("海外绘本/小老鼠迈尔斯/corrections", body))
        self.assertTrue(chunks["纠正台账 / 金句纠正"].required)
        self.assertEqual(chunks["纠正台账 / 金句纠正"].required_reason,
                         "heading:金句纠正")
        self.assertTrue(chunks["纠正台账 / 画面纠正"].required)
        self.assertFalse(chunks["纠正台账 / 迭代历史"].required)

    def test_the_core_emotional_mechanism_section_is_required(self):
        body = "# 指纹规格\n\n## 核心情感机制\n\n- 操作化定义 + core_test。\n"
        chunks = by_heading(
            marked("海外绘本/小老鼠迈尔斯/story-fingerprint-spec", body))
        self.assertTrue(chunks["指纹规格 / 核心情感机制"].required)
        self.assertEqual(chunks["指纹规格 / 核心情感机制"].required_reason,
                         "heading:核心情感机制")


class ConservativeFallbackTests(unittest.TestCase):
    def test_an_unpublished_source_is_required_in_full(self):
        chunks = marked("海外绘本/小老鼠迈尔斯/worldview", WORLDVIEW_BODY,
                        status="needs_review")
        self.assertTrue(all(chunk.required for chunk in chunks))
        self.assertEqual(chunks[0].required_reason, "source_status_not_published")

    def test_an_unsynced_index_is_required_in_full(self):
        chunks = marked("海外绘本/小老鼠迈尔斯/worldview", WORLDVIEW_BODY,
                        index_synced=False)
        self.assertTrue(all(chunk.required for chunk in chunks))
        self.assertEqual(chunks[0].required_reason, "index_not_synced")

    def test_an_unknown_page_type_is_required_in_full(self):
        chunks = marked("海外绘本/小老鼠迈尔斯/something-brand-new", WORLDVIEW_BODY)
        self.assertTrue(all(chunk.required for chunk in chunks))
        self.assertEqual(chunks[0].required_reason, "unknown_page_type")

    def test_a_key_without_a_page_type_is_required_in_full(self):
        chunks = marked("worldview", WORLDVIEW_BODY)
        self.assertTrue(all(chunk.required for chunk in chunks))

    def test_a_caller_declaration_overrides_everything_else(self):
        item = evidence(key="海外绘本/小老鼠迈尔斯/ip-overview", body=WORLDVIEW_BODY)
        chunks = mark_required(
            chunk_evidence(item), page_type="ip-overview",
            status="published", index_synced=True, declared_required=True,
        )
        self.assertTrue(all(chunk.required for chunk in chunks))
        self.assertEqual(chunks[0].required_reason, "caller_declared")


class MarkBundleTests(unittest.TestCase):
    def test_mark_bundle_marks_each_page_by_its_own_type(self):
        bundle = {"items": (
            evidence(key="海外绘本/小老鼠迈尔斯/worldview", body=WORLDVIEW_BODY),
            evidence(key="海外绘本/小老鼠迈尔斯/content-spec", body="# 规范\n\n正文。\n"),
        )}
        chunks = mark_bundle(bundle)
        by_key = {}
        for chunk in chunks:
            by_key.setdefault(chunk.key, []).append(chunk)
        self.assertFalse(by_key["海外绘本/小老鼠迈尔斯/worldview"][0].required)
        self.assertTrue(all(chunk.required
                            for chunk in by_key["海外绘本/小老鼠迈尔斯/content-spec"]))

    def test_a_declared_page_type_is_required_in_full(self):
        bundle = {"items": (
            evidence(key="海外绘本/小老鼠迈尔斯/worldview", body=WORLDVIEW_BODY),
        )}
        chunks = mark_bundle(bundle, declared_page_types=("worldview",))
        self.assertTrue(all(chunk.required for chunk in chunks))

    def test_a_declared_key_is_required_in_full(self):
        bundle = {"items": (
            evidence(key="海外绘本/小老鼠迈尔斯/worldview", body=WORLDVIEW_BODY),
        )}
        chunks = mark_bundle(bundle, declared_keys=("海外绘本/小老鼠迈尔斯/worldview",))
        self.assertTrue(all(chunk.required for chunk in chunks))


class BundleShapeTests(unittest.TestCase):
    """The loader hands back a frozen dataclass, not a mapping."""

    def test_mark_bundle_accepts_the_loader_bundle_object(self):
        bundle = KnowledgeEvidenceBundle(
            items=(KnowledgeEvidence(
                key="海外绘本/小老鼠迈尔斯/content-spec",
                doc_token="doxcnExample",
                revision_id=17,
                title="海外绘本/小老鼠迈尔斯/content-spec",
                content="# 规范\n\n正文。\n",
                source_revisions={"node-a": "17"},
            ),),
            warnings=(),
            offline=False,
            fetched_at="2026-09-23T10:30:00+08:00",
        )
        chunks = mark_bundle(bundle)
        self.assertTrue(chunks)
        self.assertTrue(all(chunk.required for chunk in chunks))
        self.assertEqual(chunks[0].required_reason, "page_type:content-spec")

    def test_a_loader_bundle_item_keeps_its_own_page_type_rules(self):
        bundle = KnowledgeEvidenceBundle(
            items=(
                KnowledgeEvidence(
                    key="海外绘本/小老鼠迈尔斯/worldview",
                    doc_token="doxcnExample",
                    revision_id=17,
                    title="海外绘本/小老鼠迈尔斯/worldview",
                    content=WORLDVIEW_BODY,
                    source_revisions={"node-a": "17"},
                ),
                KnowledgeEvidence(
                    key="海外绘本/小老鼠迈尔斯/content-spec",
                    doc_token="doxcnExample",
                    revision_id=17,
                    title="海外绘本/小老鼠迈尔斯/content-spec",
                    content="# 规范\n\n正文。\n",
                    source_revisions={"node-a": "17"},
                ),
            ),
            warnings=(),
            offline=False,
            fetched_at="2026-09-23T10:30:00+08:00",
        )
        by_key = {}
        for chunk in mark_bundle(bundle):
            by_key.setdefault(chunk.key, []).append(chunk)
        self.assertFalse(by_key["海外绘本/小老鼠迈尔斯/worldview"][0].required)
        self.assertTrue(all(chunk.required
                            for chunk in by_key["海外绘本/小老鼠迈尔斯/content-spec"]))


class UnknownProvenanceTests(unittest.TestCase):
    """A page whose provenance is absent is a page that cannot be classified."""

    def test_a_missing_status_is_required_in_full(self):
        bundle = {"items": (without(evidence(), "status"),)}
        chunks = mark_bundle(bundle)
        self.assertTrue(all(chunk.required for chunk in chunks))
        self.assertEqual(chunks[0].required_reason, "source_status_unknown")

    def test_a_missing_index_sync_flag_is_required_in_full(self):
        bundle = {"items": (without(evidence(), "index_synced"),)}
        chunks = mark_bundle(bundle)
        self.assertTrue(all(chunk.required for chunk in chunks))
        self.assertEqual(chunks[0].required_reason, "index_sync_unknown")

    def test_an_item_without_provenance_attributes_is_required_in_full(self):
        class Item:
            key = "海外绘本/小老鼠迈尔斯/worldview"
            doc_token = "doxcnExample"
            revision_id = 17
            content = WORLDVIEW_BODY

        chunks = mark_bundle({"items": (Item(),)})
        self.assertTrue(all(chunk.required for chunk in chunks))
        self.assertEqual(chunks[0].required_reason, "source_status_unknown")

    def test_an_unknown_status_is_required_when_it_reaches_marking_directly(self):
        chunks = mark_required(chunk_evidence(evidence()), page_type="worldview",
                              status=None, index_synced=True)
        self.assertTrue(all(chunk.required for chunk in chunks))
        self.assertEqual(chunks[0].required_reason, "source_status_unknown")

    def test_an_unknown_sync_flag_is_required_when_it_reaches_marking_directly(self):
        chunks = mark_required(chunk_evidence(evidence()), page_type="worldview",
                              status="published", index_synced=None)
        self.assertTrue(all(chunk.required for chunk in chunks))
        self.assertEqual(chunks[0].required_reason, "index_sync_unknown")


class FencedContentTests(unittest.TestCase):
    def test_a_yaml_comment_is_not_read_as_a_heading(self):
        # A '#' inside a fenced block is YAML, not structure: reading it as a
        # heading would split the payload away from its anchor and leave a
        # constraint block behind as an ordinary section.
        chunks = marked("海外绘本/小老鼠迈尔斯/prop-registry", FENCED_HASH_BODY)
        self.assertEqual([chunk.heading_path for chunk in chunks],
                         [("台账",), ("台账", "数据块")])
        self.assertFalse(chunks[0].required)
        self.assertTrue(chunks[1].required)
        self.assertEqual(chunks[1].required_reason, "machine_data:props")


class SwallowedHeadingTests(unittest.TestCase):
    """A hard-constraint heading swallowed by an orphan fence still protects."""

    BODY = (
        "# 世界观总纲\n\n"
        "```\n"
        "未收尾的机器块\n\n"
        "## 创作红线不变量\n\n"
        "- 迈尔斯不能飞行。\n"
        "```\n\n"
        "## 场景清单\n\n"
        "- 森林\n"
    )

    def test_a_heading_swallowed_by_an_orphan_fence_is_still_required(self):
        # The page lost the closing fence of its first block, so the next fence
        # marker closes it and every heading in between lands in a single chunk.
        # The heading path no longer names the red lines, but the heading text is
        # still inside that chunk, and that is what keeps the hard constraint out
        # of the filter's reach.
        chunks = by_heading(marked("海外绘本/小老鼠迈尔斯/worldview", self.BODY))
        swallowed = chunks["世界观总纲"]
        self.assertEqual(swallowed.heading_path, ("世界观总纲",))
        self.assertIn("## 创作红线不变量", swallowed.text)
        self.assertTrue(swallowed.required)
        self.assertEqual(swallowed.required_reason, "heading:创作红线不变量")
        self.assertFalse(chunks["世界观总纲 / 场景清单"].required)

    def test_a_prose_mention_of_a_marker_does_not_make_a_section_required(self):
        # The text scan only looks at heading lines, so a soft section keeps its
        # place in the candidate list.
        body = ("# 世界观总纲\n\n## 场景清单\n\n"
                "- 森林：迈尔斯家附近，这里不谈创作红线不变量。\n")
        chunks = by_heading(marked("海外绘本/小老鼠迈尔斯/worldview", body))
        self.assertFalse(chunks["世界观总纲 / 场景清单"].required)


class SpaceLessHeadingTests(unittest.TestCase):
    """A hard-constraint heading written without a space still protects."""

    BODY = (
        "# 世界观总纲\n\n"
        "## 核心价值主张\n\n"
        "勇气不是不害怕，而是害怕时仍然向前。\n\n"
        "##创作红线不变量\n\n"
        "- 迈尔斯不能飞行。\n\n"
        "## 场景清单\n\n"
        "- 森林\n"
    )

    def test_a_red_line_heading_written_without_a_space_is_still_required(self):
        # The structural rule needs whitespace after the hashes, so this heading
        # never opens a section of its own: the red line stays inside the text of
        # the section above it. The heading scan is what keeps that chunk, and
        # with it the red line, out of the filter's reach.
        chunks = by_heading(marked("海外绘本/小老鼠迈尔斯/worldview", self.BODY))
        carrier = chunks["世界观总纲 / 核心价值主张"]
        self.assertIn("##创作红线不变量", carrier.text)
        self.assertTrue(carrier.required)
        self.assertEqual(carrier.required_reason, "heading:创作红线不变量")
        self.assertFalse(chunks["世界观总纲 / 场景清单"].required)

    def test_a_space_less_heading_is_matched_at_any_hash_depth(self):
        # A nested constraint section is written with more hashes than its
        # ancestor, and the scan must not depend on how many of them there are.
        body = ("# 世界观总纲\n\n## 设定\n\n###创作边界\n\n- 迈尔斯不能飞行。\n")
        chunks = by_heading(marked("海外绘本/小老鼠迈尔斯/worldview", body))
        self.assertTrue(chunks["世界观总纲 / 设定"].required)
        self.assertEqual(chunks["世界观总纲 / 设定"].required_reason,
                         "heading:创作边界")


class SplitSectionTests(unittest.TestCase):
    def test_every_part_of_a_split_constraint_block_stays_required(self):
        # A machine-data payload longer than the chunk cap is split at paragraph
        # boundaries. Only the first part carries the anchor, so without
        # propagation the continuation would be a filterable block holding a
        # hard constraint.
        terms = "\n".join(f'- "禁用词{index:03d}"' for index in range(200))
        body = (
            "# 台账\n\n## 数据\n\n<!-- machine-data: redline_terms -->\n"
            f"```yaml\nredline_terms:\n{terms}\n```\n"
        )
        chunks = marked("海外绘本/小老鼠迈尔斯/corrections", body)
        parts = [chunk for chunk in chunks if chunk.heading_path == ("台账", "数据")]
        self.assertGreater(len(parts), 1)
        for part in parts:
            with self.subTest(chunk=part.chunk_id):
                self.assertLessEqual(len(part.text), MAX_CHUNK_CHARS)
                self.assertTrue(part.required)
                self.assertEqual(part.required_reason, "machine_data:redline_terms")
        # Exactly one part carries the anchor, and the last part carries none of
        # it: that one is required because of the section it belongs to.
        anchored = [part for part in parts if "machine-data: redline_terms" in part.text]
        self.assertEqual(len(anchored), 1)
        self.assertNotIn("machine-data", parts[-1].text)

    def test_a_split_soft_section_stays_soft(self):
        paragraphs = "\n\n".join(f"第 {index} 段。" + "字" * 60 for index in range(40))
        body = f"# 世界观总纲\n\n## 场景清单\n\n{paragraphs}\n"
        chunks = marked("海外绘本/小老鼠迈尔斯/worldview", body)
        plot = [chunk for chunk in chunks
                if chunk.heading_path == ("世界观总纲", "场景清单")]
        self.assertGreater(len(plot), 1)
        for chunk in plot:
            with self.subTest(chunk=chunk.chunk_id):
                self.assertFalse(chunk.required)
                self.assertIsNone(chunk.required_reason)


if __name__ == "__main__":
    unittest.main()
