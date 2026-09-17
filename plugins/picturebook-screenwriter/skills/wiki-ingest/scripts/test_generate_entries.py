# -*- coding: utf-8 -*-
"""generate_entries.py 跨文件标题查重离线单测（无网络）。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import generate_entries as ge  # noqa: E402


def _file(d, rel, title="", page_type="", project="", revision="r1",
          source_node_tokens=("node-a",), source_revision_parts=("17",)):
    p = os.path.join(d, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(f'---\ntitle: "{title}"\nseries_id: "海外绘本"\n'
                f'page_type: "{page_type}"\nproject_id: "{project}"\n'
                f'revision_id: "{revision}"\n'
                'source_feishu_url: "https://example.feishu.cn/wiki/node-a"\n'
                'extracted_at: "2026-09-17T12:00:00+08:00"\n')
        f.write("source_node_tokens:\n")
        for token in source_node_tokens:
            f.write(f'  - "{token}"\n')
        f.write("source_revision_parts:\n")
        for part in source_revision_parts:
            f.write(f'  - "{part}"\n')
        f.write("---\n\n正文内容。\n")
    return p


class TestCrossFileDup(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_canonical_duplicate_fails(self):
        _file(self.d, "common/a.md", "[common/creation-standards] 创作规范与标准",
              "creation-standards")
        _file(self.d, "common/b.md", "[common/creation-standards] 创作规范与标准",
              "creation-standards")
        files = ge.discover_files(self.d)
        issues = ge.check_cross_file_dups(files)
        self.assertEqual(len(issues), 2)  # 两文件各挂一条 FAIL
        self.assertTrue(all(lvl == "FAIL" for lvl, _ in issues))

    def test_raw_vs_bracketed_same_title_fails(self):
        # 裸标题与规范括号标题指向同一逻辑条目
        _file(self.d, "common/a.md", "创作规范与标准", "creation-standards")
        _file(self.d, "common/b.md", "[common/creation-standards] 创作规范与标准",
              "creation-standards")
        files = ge.discover_files(self.d)
        issues = ge.check_cross_file_dups(files)
        self.assertEqual(len(issues), 2)

    def test_distinct_projects_no_false_positive(self):
        _file(self.d, "projA/worldview.md", "世界观", "worldview", project="项目A")
        _file(self.d, "projB/worldview.md", "世界观", "worldview", project="项目B")
        files = ge.discover_files(self.d)
        issues = ge.check_cross_file_dups(files)
        self.assertEqual(issues, [])

    def test_unique_titles_pass(self):
        _file(self.d, "common/a.md", "同名标题", "creation-standards")
        _file(self.d, "common/b.md", "同名标题", "ip-overview")
        files = ge.discover_files(self.d)
        issues = ge.check_cross_file_dups(files)
        self.assertEqual(issues, [])

    def test_display_titles_cannot_share_logical_key(self):
        _file(self.d, "common/a.md", title="世界设定", page_type="worldview")
        _file(self.d, "common/b.md", title="另一个世界设定", page_type="worldview")
        files = ge.discover_files(self.d)
        issues = ge.check_cross_file_dups(files)
        self.assertTrue(any(level == "FAIL" and "logical key" in reason
                            for level, reason in issues))

    def test_main_validate_only_exits_1_on_dup(self):
        _file(self.d, "common/a.md", "创作规范与标准", "creation-standards")
        _file(self.d, "common/b.md", "[common/creation-standards] 创作规范与标准",
              "creation-standards")
        rc = ge.main(["--validate-only", "--staging-dir", self.d])
        self.assertEqual(rc, 1)


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_manifest_contains_source_revision_vector(self):
        candidate = _file(self.tmp.name, "worldview.md", title="世界观",
                          page_type="worldview",
                          project="小老鼠迈尔斯",
                          source_node_tokens=["node-a", "node-b"],
                          source_revision_parts=["17", "28"])
        manifest = ge.build_manifest(str(self.tmp), {candidate: []})
        entry = manifest["entries"][0]
        self.assertEqual(entry["key"], "海外绘本/小老鼠迈尔斯/worldview")
        self.assertEqual(entry["source_revisions"],
                         {"node-a": "17", "node-b": "28"})


class TestSourceRevisionVector(unittest.TestCase):
    def test_valid_vector_pairs_tokens_and_revisions(self):
        vector = ge.source_revision_vector({
            "source_node_tokens": ["node-a", "node-b"],
            "source_revision_parts": ["17", "28"],
        })
        self.assertEqual(vector, {"node-a": "17", "node-b": "28"})

    def test_unequal_lengths_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "equal lengths"):
            ge.source_revision_vector({
                "source_node_tokens": ["node-a", "node-b"],
                "source_revision_parts": ["17"],
            })

    def test_duplicate_tokens_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            ge.source_revision_vector({
                "source_node_tokens": ["node-a", "node-a"],
                "source_revision_parts": ["17", "28"],
            })

    def test_blank_token_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "blank"):
            ge.source_revision_vector({
                "source_node_tokens": [""],
                "source_revision_parts": ["17"],
            })

    def test_blank_revision_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "blank"):
            ge.source_revision_vector({
                "source_node_tokens": ["node-a"],
                "source_revision_parts": [""],
            })


if __name__ == "__main__":
    unittest.main()
