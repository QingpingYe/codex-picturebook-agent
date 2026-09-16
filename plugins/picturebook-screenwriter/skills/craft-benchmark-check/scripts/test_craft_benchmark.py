#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""craft_benchmark.py 离线单测。

覆盖：机读基准块 ↔ 内置兜底同源一致性 + 死键清零（自造复合词不做机检）/
翻页钩子候选与密度（末页豁免、无字页重置、间隔上限）/ 单页拟声词用量 /
副歌末段打破（末四分之一原样出现；成环收尾交 LLM-judge）/ 无字页位置代理
（大纲转折/高潮段）/ 无体裁时形态分档兜底 + 跨体裁指标仍输出 / 副歌强度档注 /
fixture 端到端解析事实。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import craft_benchmark as craft  # noqa: E402

FIXTURE = os.path.join(HERE, "fixture-sample-script.md")
BASELINES, _SRC = craft.load_baselines()


def _script_md(pages, outline=None, age=None):
    lines = ["# Test Book - 完整脚本", "", "## 脚本信息"]
    if age:
        lines.append(f"- 目标年龄：{age}")
    lines.append("- 文体：韵文冒险")
    if outline:
        lines += ["", "## 分页大纲",
                  "| 页码区间 | 叙事功能 | 本页目标 |", "|---|---|---|"]
        lines += [f"| {r} | {f} | x |" for r, f in outline]
    lines += ["", "## 逐页脚本", "| # | Text |", "|---|---|"]
    lines += [f"| {i} | {t} |" for i, t in enumerate(pages, 1)]
    return "\n".join(lines)


def _metrics(pages, outline=None, age=None):
    return craft.compute_metrics(craft.parse_script(_script_md(pages, outline, age)))


# 12 句互异正文：行规范化会剥掉数字/序号，数字或字母序号 filler 会让全部行
# 归一为同一行、污染副歌检测语义——需要"无副歌"或"副歌之外互异"的用例必须用互异句子
DISTINCT12 = [
    "The sun is very warm.", "We see a tall green tree.", "Little birds sing a song.",
    "White clouds drift up high.", "A soft rabbit runs past.", "The long path winds on.",
    "Rain falls on the roof top.", "A red fox hides below.", "Stars come out at nightfall.",
    "The old boat drifts along.", "Warm soup waits at home.", "Sleep comes very soon now.",
]


def _pages_with(replacements):
    """DISTINCT12 的副本，按页号覆盖：{页号: 文本}。"""
    pages = list(DISTINCT12)
    for no, text in replacements.items():
        pages[no - 1] = text
    return pages


def _row(rows, name):
    return next((r for r in rows if r["metric"] == name), None)


class TestBaselineConsistency(unittest.TestCase):
    """机读块是唯一权威；内置兜底必须逐键同源，否则双份必然漂移。"""

    def test_machine_block_matches_builtin_defaults(self):
        path = craft.default_baselines_path()
        self.assertIsNotNone(path)
        block = craft._extract_json_block(path)
        self.assertEqual(block, craft.DEFAULT_BASELINES)

    def test_machine_block_no_dead_keys(self):
        """机读块不得携带无消费者的键——自造复合词明确不做机检
        （零假阴性不可达，见 SKILL 能力边界），携带它会误导维护者改了门禁。"""
        block = craft._extract_json_block(craft.default_baselines_path())
        self.assertNotIn("coined_compound_max", block)
        self.assertNotIn("coined_compound_max", craft.DEFAULT_BASELINES)


class TestHookDensity(unittest.TestCase):
    """翻页钩子候选（页末问句/悬停省略/破折号）与最长无钩连续页。"""

    def _pages(self, hooks):
        pages = list(DISTINCT12)
        for no in hooks:
            pages[no - 1] = f"What was that sound on page {no}?"
        return pages

    def test_hook_candidates_and_max_gap(self):
        m = _metrics(self._pages({2, 9}))
        self.assertIn("hook_max_gap", m)
        self.assertIn("hook_pages", m)
        self.assertEqual(m["hook_pages"], ["2", "9"])
        # 页 3-8 连续 6 页无钩候选
        self.assertEqual(m["hook_max_gap"], 6)

    def test_dense_hooks_pass(self):
        m = _metrics(self._pages({2, 7}))
        self.assertEqual(m["hook_max_gap"], 4)
        rows, _, _ = craft.evaluate(m, BASELINES, None)
        r = _row(rows, "翻页钩子密度")
        self.assertIsNotNone(r)
        self.assertEqual(r["verdict"], "PASS")

    def test_sparse_hooks_warn(self):
        m = _metrics(self._pages({2, 9}))
        rows, _, _ = craft.evaluate(m, BASELINES, None)
        r = _row(rows, "翻页钩子密度")
        self.assertIsNotNone(r)
        self.assertEqual(r["verdict"], "WARN")

    def test_silent_page_resets_gap_and_last_page_exempt(self):
        pages = _pages_with({2: "Look! What was that?",     # 钩子在页 2
                             7: "（本页无文字）",            # 无字页 7 → 计数重置
                             9: "Is it safe to cross?"})    # 钩子在页 9
        m = _metrics(pages)
        # 页 1 连 1 页、页 3-6 连 4 页、页 8 连 1 页、页 10-11 连 2 页（末页豁免）
        self.assertEqual(m["hook_max_gap"], 4)

    def test_ellipsis_and_dash_are_hooks(self):
        pages = _pages_with({4: "Until one day...", 8: "But then —"})
        m = _metrics(pages)
        self.assertEqual(m["hook_pages"], ["4", "8"])


class TestPerPageOnomatopoeia(unittest.TestCase):
    """单页拟声词用量：逐页计数，超单页上限的页要浮出。"""

    def test_over_budget_page_reported(self):
        pages = _pages_with({3: "Crunch, crunch, crunch! WHOOSH! SPLAT!"})
        m = _metrics(pages)
        self.assertIn("ono_per_page", m)
        entry = next((e for e in m["ono_per_page"] if e[0] == "3"), None)
        self.assertIsNotNone(entry)
        self.assertGreaterEqual(entry[1], 3)
        rows, _, _ = craft.evaluate(m, BASELINES, None)
        r = _row(rows, "单页拟声词用量")
        self.assertIsNotNone(r)
        self.assertEqual(r["verdict"], "WARN")
        self.assertIn("3", str(r["value"]))

    def test_normal_pages_pass(self):
        pages = _pages_with({2: "Stomp, stomp, stomp."})
        m = _metrics(pages)
        self.assertIn("ono_per_page", m)
        rows, _, _ = craft.evaluate(m, BASELINES, None)
        r = _row(rows, "单页拟声词用量")
        self.assertIsNotNone(r)
        self.assertEqual(r["verdict"], "PASS")


class TestRefrainBreak(unittest.TestCase):
    """副歌末段打破：最高频重复行不得在末四分之一页原样出现（成环收尾除外）。"""

    REFRAIN = "We're going on a bear hunt."

    def _pages(self, late):
        # 前 4 页副歌 ×4，页 5-10 互异正文，页 11 是末四分之一首段（12 页书 → 末 3 页）
        pages = [self.REFRAIN] * 4 + DISTINCT12[4:10]
        pages.append(late)                    # 页 11
        pages.append("Home at last today.")   # 页 12
        return pages

    def test_late_verbatim_warns(self):
        m = _metrics(self._pages(self.REFRAIN))
        self.assertIn("refrain_late_verbatim", m)
        self.assertEqual(m["refrain_late_verbatim"], ["11"])
        rows, _, _ = craft.evaluate(m, BASELINES, None)
        r = _row(rows, "副歌末段打破")
        self.assertIsNotNone(r)
        self.assertEqual(r["verdict"], "WARN")

    def test_late_variant_passes(self):
        m = _metrics(self._pages("We went on a bear hunt."))
        self.assertEqual(m["refrain_late_verbatim"], [])
        rows, _, _ = craft.evaluate(m, BASELINES, None)
        r = _row(rows, "副歌末段打破")
        self.assertIsNotNone(r)
        self.assertEqual(r["verdict"], "PASS")

    def test_ring_closing_page_is_exempt(self):
        """成环收尾（末页复现开篇句）是 C7.3 技法，不按末段原样出现报偏离。"""
        pages = self._pages("We went on a bear hunt.")
        pages[11] = "We're going on a bear hunt."   # 页 12 复现首句
        m = _metrics(pages)
        self.assertEqual(m["refrain_late_verbatim"], [])
        rows, _, _ = craft.evaluate(m, BASELINES, None)
        r = _row(rows, "副歌末段打破")
        self.assertIsNotNone(r)
        self.assertEqual(r["verdict"], "PASS")

    def test_no_refrain_is_info(self):
        m = _metrics(list(DISTINCT12))
        rows, _, _ = craft.evaluate(m, BASELINES, None)
        r = _row(rows, "副歌末段打破")
        self.assertIsNotNone(r)
        self.assertEqual(r["verdict"], "INFO")


class TestSilentPosition(unittest.TestCase):
    """无字页位置：以分页大纲的转折/高潮段为情绪顶点代理。"""

    OUTLINE = [("1-3", "建立"), ("4-9", "发展"), ("10-11", "高潮"), ("12", "尾声")]

    def test_misplaced_silent_warns(self):
        m = _metrics(_pages_with({7: "（本页无文字）"}), outline=self.OUTLINE)
        self.assertIn("silent_misplaced", m)
        self.assertEqual(m["silent_misplaced"], ["7"])
        rows, _, _ = craft.evaluate(m, BASELINES, None)
        r = _row(rows, "无字页位置")
        self.assertIsNotNone(r)
        self.assertEqual(r["verdict"], "WARN")

    def test_peak_silent_passes(self):
        m = _metrics(_pages_with({10: "（本页无文字）"}), outline=self.OUTLINE)
        self.assertEqual(m["silent_misplaced"], [])
        rows, _, _ = craft.evaluate(m, BASELINES, None)
        r = _row(rows, "无字页位置")
        self.assertIsNotNone(r)
        self.assertEqual(r["verdict"], "PASS")

    def test_no_outline_is_info(self):
        m = _metrics(_pages_with({7: "（本页无文字）"}))
        self.assertIn("silent_misplaced", m)
        rows, _, _ = craft.evaluate(m, BASELINES, None)
        r = _row(rows, "无字页位置")
        self.assertIsNotNone(r)
        self.assertEqual(r["verdict"], "INFO")


class TestNoGenreFallback(unittest.TestCase):
    """未指定体裁：形态分档兜底可用，且跨体裁通用项不因早退而缺失。"""

    def _fixture_metrics(self):
        return craft.compute_metrics(craft.parse_script(craft.read_text(FIXTURE)))

    def test_form_classification(self):
        m = self._fixture_metrics()
        rows, _, g = craft.evaluate(m, BASELINES, None)
        self.assertIsNone(g)
        r = _row(rows, "形态分档")
        self.assertIsNotNone(r)
        self.assertIn("极简型", str(r["value"]))

    def test_crossgenre_rows_still_shown_without_genre(self):
        m = self._fixture_metrics()
        rows, _, _ = craft.evaluate(m, BASELINES, None)
        for name in ("高潮页文字行数", "慢写/大写排版处数", "首尾回环度",
                     "翻页钩子密度", "副歌末段打破", "单页拟声词用量", "无字页位置"):
            self.assertIsNotNone(_row(rows, name), name)

    def test_refrain_strength_tier_note(self):
        m = self._fixture_metrics()
        rows, _, _ = craft.evaluate(m, BASELINES, "韵文冒险")
        r = _row(rows, "最高频重复行次数")
        self.assertIsNotNone(r)
        self.assertIn("微弱", r["note"])


class TestFixtureFacts(unittest.TestCase):
    """fixture 端到端解析事实锁（防解析回归）。"""

    def test_parse_facts(self):
        parsed = craft.parse_script(craft.read_text(FIXTURE))
        m = craft.compute_metrics(parsed)
        self.assertEqual(parsed["title"], "The Brave Little Bridge")
        self.assertEqual(m["page_total"], 12)
        self.assertEqual(m["silent_pages"], ["7"])
        self.assertEqual(m["refrain_top_freq"], 4)
        self.assertEqual(m["climax_lines"], 4)


if __name__ == "__main__":
    unittest.main()
