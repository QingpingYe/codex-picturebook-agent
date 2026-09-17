# -*- coding: utf-8 -*-
"""scan_external_links.py 离线单测（无网络、无 lark-cli）。

覆盖协议层：URL 提取三形态 / 中文标点剥离 / 归一化 / 一层硬排除 /
二层廉价打分 / kb_node_index 内部匹配 / 四层裁剪 / 分流 / 跨文档去重 /
SCAN_DONE 证据行。
"""
import json
import os
import sys
import tempfile
import unittest
import io
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scan_external_links as sel  # noqa: E402

KB_URL = "https://bytedance.feishu.cn/wiki/KBROOTTOKEN00000000000001"

EXT = {
    "denyDomains": ["blocked.example.com"],
    "denyExtensions": [".zip", ".exe"],
    "trackingParams": ["from", "utm_source"],
    "allowDomains": ["docs.official.example"],
    "feishuDomains": ["feishu.cn", "my.feishu.cn"],
    "feishuDocPathPrefixes": ["/wiki/", "/docx/", "/docs/"],
    "feishuNonDocPathPrefixes": ["/base/"],
    "substanceKeywords": ["guide", "spec", "manual"],
    "citationIntentKeywords": ["see", "ref"],
    "maxUrlsPerDoc": 15,
    "maxUrlsPerSync": 60,
    "maxContentCharsPerUrl": 20000,
}


def _mk_settings():
    return {"feishuKnowledgeBase": {"url": KB_URL},
            "externalLinks": dict(EXT)}


class TestExtractUrls(unittest.TestCase):
    def test_three_forms_all_captured(self):
        text = (
            "See [official guide](https://docs.official.example/guide) and "
            "<https://example.com/angle> plus bare https://bare.example/path."
        )
        hits = sel.extract_urls(text, "doc1")
        urls = [h["url"] for h in hits]
        self.assertEqual(urls, [
            "https://docs.official.example/guide",
            "https://example.com/angle",
            "https://bare.example/path",
        ])

    def test_bare_url_trailing_punct_stripped(self):
        text = "参考：https://example.com/a/b）。详见 https://example.com/c, 以及 https://example.com/d；"
        hits = sel.extract_urls(text, "doc1")
        urls = [h["url"] for h in hits]
        self.assertEqual(urls, [
            "https://example.com/a/b",
            "https://example.com/c",
            "https://example.com/d",
        ])

    def test_context_window_and_offset(self):
        pad = "x" * 130
        url = "https://example.com/target"
        text = pad + url + pad
        hits = sel.extract_urls(text, "doc1")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["char_offset"], 130)
        self.assertEqual(hits[0]["host_doc_id"], "doc1")
        self.assertLessEqual(len(hits[0]["context_window"]), 130 * 2 + len(url))


class TestNormalize(unittest.TestCase):
    def test_tracking_and_case_port_hash(self):
        raw = "HTTPS://Example.com:443/Path/?utm_source=x&b=2&a=1#frag"
        key = sel.normalize_url(raw, EXT["trackingParams"])
        self.assertEqual(key, "https://example.com/path?a=1&b=2")

    def test_hash_stripped_for_key(self):
        raw = "https://example.com/docs#section-2"
        self.assertEqual(sel.normalize_url(raw, []), "https://example.com/docs")

    def test_percent_hex_uppercase(self):
        self.assertEqual(sel.normalize_url("https://example.com/a%2fb", []),
                         "https://example.com/a%2Fb")


class TestHardExclude(unittest.TestCase):
    def test_non_http_scheme(self):
        excluded, _ = sel.hard_exclude("mailto:x@y.com", EXT, KB_URL)
        self.assertTrue(excluded)

    def test_deny_domain(self):
        excluded, reason = sel.hard_exclude("https://blocked.example.com/a", EXT, KB_URL)
        self.assertTrue(excluded)
        self.assertIn("denyDomains", reason)

    def test_deny_extension(self):
        excluded, _ = sel.hard_exclude("https://example.com/tool.zip", EXT, KB_URL)
        self.assertTrue(excluded)

    def test_kb_url_itself(self):
        excluded, _ = sel.hard_exclude(KB_URL, EXT, KB_URL)
        self.assertTrue(excluded)


class TestCheapScore(unittest.TestCase):
    def test_bare_root_dropped(self):
        hit = {"url": "https://example.com", "anchor_text": "", "context_window": ""}
        keep, signals, _ = sel.cheap_score(hit, EXT)
        self.assertFalse(keep)
        self.assertEqual(signals, 0)

    def test_path_depth_keeps(self):
        hit = {"url": "https://example.com/some/path", "anchor_text": "", "context_window": ""}
        keep, signals, _ = sel.cheap_score(hit, EXT)
        self.assertTrue(keep)
        self.assertGreaterEqual(signals, 1)

    def test_allow_domain_direct_pass(self):
        hit = {"url": "https://docs.official.example", "anchor_text": "", "context_window": ""}
        keep, signals, _ = sel.cheap_score(hit, EXT)
        self.assertTrue(keep)
        self.assertGreaterEqual(signals, 1)

    def test_substance_keyword_in_path(self):
        hit = {"url": "https://example.com/manual", "anchor_text": "", "context_window": ""}
        keep, signals, _ = sel.cheap_score(hit, EXT)
        self.assertTrue(keep)
        self.assertGreaterEqual(signals, 1)


class TestKbInternal(unittest.TestCase):
    def test_token_substring_matches(self):
        tokens = {"NODEABCDEFGHIJKLMNOPQRSTUVWX", "OBJTOKEN1234567890ABCDEFGHIJ"}
        domains = EXT["feishuDomains"]
        self.assertTrue(sel.kb_internal(
            "https://bytedance.feishu.cn/wiki/NODEABCDEFGHIJKLMNOPQRSTUVWX", tokens, domains))
        self.assertFalse(sel.kb_internal(
            "https://example.com/wiki/NODEABCDEFGHIJKLMNOPQRSTUVWX", tokens, domains))
        self.assertTrue(sel.kb_internal(
            "https://bytedance.feishu.cn/file/OBJTOKEN1234567890ABCDEFGHIJ", tokens, domains))


class TestRoute(unittest.TestCase):
    def test_feishu_doc_path(self):
        self.assertEqual(sel.route_channel("https://my.feishu.cn/wiki/ABC123", EXT), "feishu")

    def test_feishu_non_doc_path_skipped(self):
        self.assertEqual(sel.route_channel("https://my.feishu.cn/base/ABC123", EXT), "skip")

    def test_web_default(self):
        self.assertEqual(sel.route_channel("https://example.com/a", EXT), "web")


class TestDedupeAndCap(unittest.TestCase):
    def test_cross_doc_dedupe_referenced_by(self):
        hits = [
            {"url": "https://example.com/shared", "host_doc_id": "doc1", "anchor_text": "a", "context_window": "", "char_offset": 0},
            {"url": "https://example.com/shared", "host_doc_id": "doc2", "anchor_text": "b", "context_window": "", "char_offset": 5},
        ]
        uniques = sel.dedupe_global(hits)
        self.assertEqual(len(uniques), 1)
        self.assertEqual(sorted(uniques[0]["referenced_by"]), ["doc1", "doc2"])

    def test_cap_orders_allow_domain_first(self):
        candidates = [
            {"url": "https://example.com/low", "allow": False, "signals": 1, "occurrences": 1, "first_pos": 10},
            {"url": "https://docs.official.example/high", "allow": True, "signals": 0, "occurrences": 1, "first_pos": 50},
        ]
        capped = sel.rank_and_cap(candidates, {"maxUrlsPerDoc": 15, "maxUrlsPerSync": 1})
        self.assertEqual(len(capped), 1)
        self.assertIn("docs.official.example", capped[0]["url"])

    def test_cap_limits_per_doc(self):
        candidates = [
            {"url": f"https://example.com/p{i}", "allow": False, "signals": 1,
             "occurrences": 1, "first_pos": i, "host_doc_id": "doc1"}
            for i in range(5)
        ]
        capped = sel.rank_and_cap(candidates, {"maxUrlsPerDoc": 3, "maxUrlsPerSync": 60})
        self.assertEqual(len(capped), 3)
        # first_pos 小的排前面
        self.assertIn("/p0", capped[0]["url"])


class TestScanMain(unittest.TestCase):
    def _build_fixture(self):
        d = tempfile.mkdtemp()
        texts = os.path.join(d, "texts")
        os.makedirs(texts)
        with open(os.path.join(texts, "doc1.md"), "w", encoding="utf-8") as f:
            f.write("正文 [link](https://docs.official.example/guide) 与 https://example.com/other 链接。")
        meta = {"doc1": {"title": "Doc One", "url": KB_URL, "obj_type": "docx"}}
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        snap = {"_kb_url": KB_URL, "nodes": {"doc1": {"obj_token": "OBJTOKEN1234567890ABCDEFGHIJ"}}}
        with open(os.path.join(d, "snap.json"), "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        settings = _mk_settings()
        with open(os.path.join(d, "settings.json"), "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False)
        return d

    def test_scan_done_line_format(self):
        d = self._build_fixture()
        out = os.path.join(d, "out.json")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = sel.main(["--texts-dir", os.path.join(d, "texts"),
                           "--meta", os.path.join(d, "meta.json"),
                           "--snapshot", os.path.join(d, "snap.json"),
                           "--settings", os.path.join(d, "settings.json"),
                           "--out", out])
        self.assertEqual(rc, 0)
        line = buf.getvalue()
        self.assertIn("SCAN_DONE:", line)
        self.assertIn("2 raw", line)
        self.assertIn("2 unique", line)
        self.assertIn("0 feishu", line)
        self.assertIn("2 web", line)
        result = json.load(open(out, encoding="utf-8"))
        self.assertEqual(len(result["pending_llm"]), 2)

    def test_zero_urls_prints_verified_zero(self):
        d = self._build_fixture()
        with open(os.path.join(d, "texts", "doc1.md"), "w", encoding="utf-8") as f:
            f.write("纯文本，无任何链接。")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = sel.main(["--texts-dir", os.path.join(d, "texts"),
                           "--meta", os.path.join(d, "meta.json"),
                           "--snapshot", os.path.join(d, "snap.json"),
                           "--settings", os.path.join(d, "settings.json"),
                           "--out", os.path.join(d, "out.json")])
        self.assertEqual(rc, 0)
        self.assertIn("0 raw", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
