import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from page_codec import PageCodecError, logical_key, parse_candidate, parse_remote_page, render_remote_page


class PageCodecTests(unittest.TestCase):
    def test_remote_page_round_trip_excludes_system_metadata(self):
        text = render_remote_page(
            "# 小老鼠世界观\n\n正文。\n",
            {"key": "海外绘本/小老鼠迈尔斯/worldview", "page_type": "worldview",
             "source_node_tokens": ["K8EXw4Ja2i7mGnk1Tvgc4zcknkd"],
             "source_revisions": {"K8EXw4Ja2i7mGnk1Tvgc4zcknkd": "r3"},
             "last_ai_revision_id": 12},
        )
        page = parse_remote_page(text)
        self.assertEqual(page.body, "# 小老鼠世界观\n\n正文。\n")
        self.assertEqual(page.metadata["key"], "海外绘本/小老鼠迈尔斯/worldview")
        self.assertEqual(page.metadata["schema_version"], 1)

    def test_remote_page_accepts_blank_line_before_metadata_json(self):
        text = render_remote_page(
            "# 小老鼠世界观\n\n正文。\n",
            {"key": "海外绘本/小老鼠迈尔斯/worldview", "page_type": "worldview",
             "source_node_tokens": ["K8EXw4Ja2i7mGnk1Tvgc4zcknkd"],
             "source_revisions": {"K8EXw4Ja2i7mGnk1Tvgc4zcknkd": "r3"},
             "last_ai_revision_id": 12},
        ).replace("## 系统元数据（请勿编辑）\n```json", "## 系统元数据（请勿编辑）\n\n```json")
        page = parse_remote_page(text)
        self.assertEqual(page.metadata["key"], "海外绘本/小老鼠迈尔斯/worldview")

    def test_metadata_must_be_final_section(self):
        broken = "## 系统元数据（请勿编辑）\n```json\n{}\n```\n\n正文"
        with self.assertRaisesRegex(PageCodecError, "final section"):
            parse_remote_page(broken)

    def test_candidate_frontmatter_becomes_metadata_and_is_removed_from_reader_body(self):
        candidate = """---
series_id: 海外绘本
project_id: 小老鼠迈尔斯
page_type: worldview
source_node_tokens:
  - source-a
source_revision_parts:
  - r11
last_ai_revision_id: 9
---
# 世界观

正文。
"""
        parsed = parse_candidate(candidate)
        self.assertEqual(logical_key(parsed), "海外绘本/小老鼠迈尔斯/worldview")
        page = parse_remote_page(render_remote_page(parsed.body, parsed.metadata))
        self.assertNotIn("series_id", page.body)
        self.assertEqual(page.metadata["key"], "海外绘本/小老鼠迈尔斯/worldview")

    def test_invalid_metadata_fails_closed(self):
        with self.assertRaisesRegex(PageCodecError, "source_node_tokens"):
            render_remote_page("# 正文\n", {
                "key": "s/p/worldview", "page_type": "worldview",
                "source_node_tokens": ["source", "source"],
                "source_revisions": {"source": "r1"}, "last_ai_revision_id": 1,
            })

    def test_machine_yaml_in_body_is_preserved(self):
        body = "# 正文\n\n```yaml\nmachine: true\n```\n"
        self.assertEqual(parse_remote_page(render_remote_page(body, {
            "key": "s/p/worldview", "page_type": "worldview",
            "source_node_tokens": ["source"], "source_revisions": {"source": "r1"}, "last_ai_revision_id": 1,
        })).body, body)

    def test_unknown_resource_indication_is_not_round_trippable(self):
        page = parse_remote_page(render_remote_page("# 正文\n\n[资源](https://example.test/a)\n", {
            "key": "s/p/worldview", "page_type": "worldview",
            "source_node_tokens": ["source"], "source_revisions": {"source": "r1"}, "last_ai_revision_id": 1,
        }))
        self.assertTrue(page.has_non_roundtrippable_content)

    def test_candidate_accepts_known_hyphenated_page_types_and_inline_tokens(self):
        candidate = parse_candidate("""---
series_id: s
project_id: p
page_type: creation-standards
source_node_tokens: [source-a, source-b]
source_revision_parts: [r1, r2]
---
# 标准
""")
        self.assertEqual(candidate.metadata["source_node_tokens"], ["source-a", "source-b"])
        self.assertEqual(candidate.metadata["source_revisions"], {"source-a": "r1", "source-b": "r2"})

    def test_source_revision_parts_make_a_recoverable_vector(self):
        candidate = parse_candidate("""---
series_id: s
project_id: p
page_type: worldview
source_node_tokens:
  - source-a
  - source-b
source_revision_parts:
  - r11
  - r22
---
# 正文
""")
        self.assertEqual(candidate.metadata["source_revisions"], {"source-a": "r11", "source-b": "r22"})


if __name__ == "__main__":
    unittest.main()


class SourceEditTimesTests(unittest.TestCase):
    def candidate(self, extra: str = "") -> str:
        return f"""---
series_id: s
project_id: p
page_type: worldview
source_node_tokens: [a, b]
source_revision_parts: [r1, r2]
{extra}---
body
"""

    def metadata(self, edit_times=...):
        value = {"key": "s/p/worldview", "page_type": "worldview",
                 "source_node_tokens": ["a", "b"], "source_revisions": {"a": "r1", "b": "r2"},
                 "last_ai_revision_id": 1}
        if edit_times is not ...:
            value["source_edit_times"] = edit_times
        return value

    def test_candidate_optional_edit_times(self):
        absent = parse_candidate(self.candidate()).metadata
        self.assertIsNone(absent["source_edit_times"])
        valid = parse_candidate(self.candidate("source_edit_time_parts: [100, 200]\n")).metadata
        self.assertEqual(valid["source_edit_times"], {"a": 100, "b": 200})

    def test_candidate_rejects_bad_edit_time_parts(self):
        for parts in ("[100]", "[0, 2]", "[-1, 2]", "[true, 2]"):
            with self.subTest(parts=parts), self.assertRaises(PageCodecError):
                parse_candidate(self.candidate(f"source_edit_time_parts: {parts}\n"))

    def test_page_edit_times_missing_valid_and_invalid(self):
        legacy = parse_remote_page(render_remote_page("body", self.metadata()))
        self.assertNotIn("source_edit_times", legacy.metadata)
        valid = parse_remote_page(render_remote_page("body", self.metadata({"a": 100, "b": 200})))
        self.assertEqual(valid.metadata["source_edit_times"], {"a": 100, "b": 200})
        for invalid in ({"a": 100}, {"a": 0, "b": 200}, {"a": True, "b": 200}, [100, 200]):
            with self.subTest(invalid=invalid), self.assertRaises(PageCodecError):
                render_remote_page("body", self.metadata(invalid))
