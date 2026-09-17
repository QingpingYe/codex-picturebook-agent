#!/usr/bin/env python3
"""check_delta.py 离线单测（unittest，无网络，无 lark-cli 依赖）

用法：
    python test_check_delta.py

覆盖：
- first_run（状态缺失 bootstrap）/ new / changed（edit_time 变化、重命名）/
  unchanged（edit_time+title 相等 + 缓存哈希校验通过）/ deleted / unknown（edit_time 缺失）
- 缓存缺失 / 缓存哈希不符 → 升级 changed（缓存损坏防御）
- docx/wiki 型：缓存 .md 文本哈希校验；revision_id="N/A" → changed（历史 N/A 修复）
- force_full → 全部 changed（逃生舱）
- schema 异常 → 逐节点 unknown fail-safe；顶层畸形 → ValueError
- finalize：merge_state 合并（changed 更新 / unchanged 保留 / deleted 移除 /
  失败节点保留旧条目或省略）/ write_atomic 原子写
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_delta as cd  # noqa: E402

EDIT = 1756572300000
NOW = "2026-08-25T00:00:00+08:00"


def sha(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def snap_node(token, title=None, obj_type="file", ext=".docx",
              edit_time_ms=EDIT, outside_kb=False):
    return {
        "title": title or f"{token}.docx",
        "url": f"https://bytedance.feishu.cn/wiki/{token}",
        "obj_token": f"obj-{token}",
        "obj_type": obj_type,
        "ext": ext,
        "edit_time_ms": edit_time_ms,
        "edit_time_src": "node_get_obj_edit_time",
        "outside_kb": outside_kb,
    }


def state_entry(token, title, obj_type="file", ext=".docx", edit_time_ms=EDIT,
                feishu_hash=None, revision_id=None, cache_file=None):
    return {
        "title": title,
        "url": f"https://bytedance.feishu.cn/wiki/{token}",
        "obj_token": f"obj-{token}",
        "obj_type": obj_type,
        "ext": ext,
        "edit_time_ms": edit_time_ms,
        "edit_time_src": "node_get_obj_edit_time",
        "feishu_revision_id": revision_id,
        "feishu_hash": feishu_hash,
        "cache_file": cache_file or f"feishu_sync_cache/{token}{ext}",
        "last_downloaded_at": "2026-08-24T20:30:00+08:00",
    }


class CacheDir:
    """写缓存文件的临时目录，自动清理"""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name

    def write(self, name, data):
        p = os.path.join(self.path, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if isinstance(data, str):
            data = data.encode("utf-8")
        with open(p, "wb") as f:
            f.write(data)
        return p

    def close(self):
        self.tmp.cleanup()


class TestClassify(unittest.TestCase):

    def setUp(self):
        self.cache = CacheDir()

    def tearDown(self):
        self.cache.close()

    def test_first_run_when_state_missing(self):
        snap = {"_kb_url": "x", "_space_id": "s",
                "nodes": {"tokA": snap_node("tokA"), "tokB": snap_node("tokB")}}
        result = cd.classify(snap, None, self.cache.path)
        self.assertTrue(result["first_run"])
        self.assertEqual(result["verdicts"]["tokA"]["verdict"], "first_run")
        self.assertEqual(result["verdicts"]["tokB"]["verdict"], "first_run")
        self.assertEqual(result["summary"], {
            "total": 2, "skip": 0, "process": 2, "new": 2,
            "changed": 0, "deleted": 0, "unknown": 0, "first_run": True,
        })
        line = cd.build_summary_line(result["summary"])
        self.assertIn("DELTA: 2 nodes → skip 0 / process 2 / new 2 / deleted 0 / unknown 0", line)
        self.assertTrue(line.endswith(" / first_run"))

    def test_unchanged_when_edit_time_and_title_equal_and_cache_hash_ok(self):
        data = b"hello world"
        h = sha(data)
        snap = {"nodes": {"tokA": snap_node("tokA")}}
        state = {"_version": 1,
                 "nodes": {"tokA": state_entry("tokA", "tokA.docx", feishu_hash=h)}}
        self.cache.write("tokA.docx", data)
        result = cd.classify(snap, state, self.cache.path)
        self.assertEqual(result["verdicts"]["tokA"]["verdict"], "unchanged")
        self.assertEqual(result["summary"]["skip"], 1)
        self.assertEqual(result["summary"]["process"], 0)

    def test_changed_when_edit_time_differs(self):
        snap = {"nodes": {"tokA": snap_node("tokA", edit_time_ms=EDIT + 5000)}}
        state = {"_version": 1,
                 "nodes": {"tokA": state_entry("tokA", "tokA.docx", edit_time_ms=EDIT)}}
        result = cd.classify(snap, state, self.cache.path)
        self.assertEqual(result["verdicts"]["tokA"]["verdict"], "changed")

    def test_changed_when_title_differs_but_edit_time_equal(self):
        snap = {"nodes": {"tokA": snap_node("tokA", title="renamed.docx")}}
        state = {"_version": 1,
                 "nodes": {"tokA": state_entry("tokA", "tokA.docx", edit_time_ms=EDIT)}}
        result = cd.classify(snap, state, self.cache.path)
        verdict = result["verdicts"]["tokA"]
        self.assertEqual(verdict["verdict"], "changed")
        self.assertIn("title", verdict["reason"])

    def test_unknown_when_edit_time_missing(self):
        snap = {"nodes": {"tokA": snap_node("tokA", edit_time_ms=None)}}
        state = {"_version": 1,
                 "nodes": {"tokA": state_entry("tokA", "tokA.docx", edit_time_ms=EDIT)}}
        result = cd.classify(snap, state, self.cache.path)
        self.assertEqual(result["verdicts"]["tokA"]["verdict"], "unknown")
        self.assertEqual(result["summary"]["unknown"], 1)

    def test_deleted_when_in_state_but_not_snapshot(self):
        snap = {"nodes": {"tokA": snap_node("tokA")}}
        state = {"_version": 1,
                 "nodes": {
                     "tokA": state_entry("tokA", "tokA.docx"),
                     "tokX": state_entry("tokX", "gone.docx"),
                 }}
        result = cd.classify(snap, state, self.cache.path)
        self.assertEqual(result["verdicts"]["tokX"]["verdict"], "deleted")
        self.assertEqual(result["summary"]["deleted"], 1)
        self.assertEqual(result["summary"]["total"], 2)

    def test_new_when_in_snapshot_but_not_state(self):
        snap = {"nodes": {"tokB": snap_node("tokB")}}
        state = {"_version": 1,
                 "nodes": {"tokA": state_entry("tokA", "tokA.docx")}}
        result = cd.classify(snap, state, self.cache.path)
        self.assertEqual(result["verdicts"]["tokB"]["verdict"], "new")
        self.assertEqual(result["summary"]["new"], 1)

    def test_cache_missing_upgrades_to_changed(self):
        snap = {"nodes": {"tokA": snap_node("tokA")}}
        state = {"_version": 1,
                 "nodes": {"tokA": state_entry("tokA", "tokA.docx", feishu_hash=sha(b"x"))}}
        # 不写缓存文件
        result = cd.classify(snap, state, self.cache.path)
        verdict = result["verdicts"]["tokA"]
        self.assertEqual(verdict["verdict"], "changed")
        self.assertIn("cache", verdict["reason"])

    def test_cache_hash_mismatch_upgrades_to_changed(self):
        snap = {"nodes": {"tokA": snap_node("tokA")}}
        state = {"_version": 1,
                 "nodes": {"tokA": state_entry("tokA", "tokA.docx", feishu_hash=sha(b"good"))}}
        self.cache.write("tokA.docx", b"corrupted")
        result = cd.classify(snap, state, self.cache.path)
        verdict = result["verdicts"]["tokA"]
        self.assertEqual(verdict["verdict"], "changed")
        self.assertIn("hash", verdict["reason"])

    def test_force_full_all_changed(self):
        snap = {"nodes": {"tokA": snap_node("tokA")}}
        state = {"_version": 1,
                 "nodes": {"tokA": state_entry("tokA", "tokA.docx", feishu_hash=sha(b"x"))}}
        self.cache.write("tokA.docx", b"x")
        result = cd.classify(snap, state, self.cache.path, force_full=True)
        self.assertEqual(result["verdicts"]["tokA"]["verdict"], "changed")

    def test_doc_type_unchanged_via_cached_markdown(self):
        md = "# 标题\n正文内容"
        h = sha(md)
        snap = {"nodes": {"tokD": snap_node("tokD", title="原生文档", obj_type="docx", ext=".md")}}
        state = {"_version": 1,
                 "nodes": {"tokD": state_entry("tokD", "原生文档", obj_type="docx", ext=".md",
                                               feishu_hash=h, revision_id="80")}}
        self.cache.write("tokD.md", md)
        result = cd.classify(snap, state, self.cache.path)
        self.assertEqual(result["verdicts"]["tokD"]["verdict"], "unchanged")

    def test_doc_type_with_na_revision_flagged_changed(self):
        md = "# 标题\n正文内容"
        h = sha(md)
        snap = {"nodes": {"tokD": snap_node("tokD", title="原生文档", obj_type="docx", ext=".md")}}
        state = {"_version": 1,
                 "nodes": {"tokD": state_entry("tokD", "原生文档", obj_type="docx", ext=".md",
                                               feishu_hash=h, revision_id="N/A")}}
        self.cache.write("tokD.md", md)
        result = cd.classify(snap, state, self.cache.path)
        verdict = result["verdicts"]["tokD"]
        self.assertEqual(verdict["verdict"], "changed")
        self.assertIn("N/A", verdict["reason"])

    def test_schema_error_in_single_node_fails_safe_to_unknown(self):
        snap = {"nodes": {"tokA": "garbage-not-a-dict"}}
        state = {"_version": 1,
                 "nodes": {"tokA": state_entry("tokA", "tokA.docx")}}
        result = cd.classify(snap, state, self.cache.path)
        self.assertEqual(result["verdicts"]["tokA"]["verdict"], "unknown")
        self.assertIn("schema", result["verdicts"]["tokA"]["reason"])

    def test_malformed_top_level_raises_value_error(self):
        snap = {"nodes": ["not", "a", "dict"]}
        state = {"_version": 1, "nodes": {}}
        with self.assertRaises(ValueError):
            cd.classify(snap, state, self.cache.path)

    def test_mixed_summary_counts_add_up(self):
        data = b"content"
        h = sha(data)
        snap = {"nodes": {
            "tokA": snap_node("tokA"),                # unchanged
            "tokB": snap_node("tokB"),                # new
            "tokC": snap_node("tokC", edit_time_ms=None),  # unknown
        }}
        state = {"_version": 1, "nodes": {
            "tokA": state_entry("tokA", "tokA.docx", feishu_hash=h),
            "tokC": state_entry("tokC", "tokC.docx", edit_time_ms=EDIT),  # 快照 edit_time 缺失 → unknown
            "tokX": state_entry("tokX", "gone.docx"),  # deleted
        }}
        self.cache.write("tokA.docx", data)
        result = cd.classify(snap, state, self.cache.path)
        summary = result["summary"]
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["skip"], 1)
        self.assertEqual(summary["new"], 1)
        self.assertEqual(summary["unknown"], 1)
        self.assertEqual(summary["deleted"], 1)
        self.assertEqual(summary["process"], 2)
        self.assertEqual(summary["total"], summary["skip"] + summary["deleted"] + summary["process"])


class TestMergeState(unittest.TestCase):

    def setUp(self):
        self.cache = CacheDir()

    def tearDown(self):
        self.cache.close()

    def _snap(self):
        return {"_kb_url": "kb", "_space_id": "sp", "nodes": {
            "tokC": snap_node("tokC", edit_time_ms=EDIT + 5000),   # changed
            "tokU": snap_node("tokU"),                              # unchanged
            "tokN": snap_node("tokN"),                              # new, hash present
            "tokF": snap_node("tokF"),                              # new, download failed (no hash)
            "tokD": snap_node("tokD", title="原生文档", obj_type="docx", ext=".md"),  # doc, changed
        }}

    def _state(self, cache_data=b"old"):
        h = sha(cache_data)
        return {"_version": 1, "_kb_url": "kb", "_space_id": "sp",
                "_last_sync_at": "2026-08-24T20:00:00+08:00", "nodes": {
                    "tokC": state_entry("tokC", "tokC.docx", edit_time_ms=EDIT, feishu_hash=h),
                    "tokU": state_entry("tokU", "tokU.docx", edit_time_ms=EDIT, feishu_hash=h),
                    "tokX": state_entry("tokX", "gone.docx", feishu_hash=h),
                }}

    def test_merge_updates_changed_keeps_unchanged_removes_deleted(self):
        h_new = sha(b"new content")
        h_old = sha(b"old")
        state = self._state(cache_data=b"old")
        self.cache.write("tokU.docx", b"old")  # unchanged 需要缓存校验通过
        merged = cd.merge_state(
            self._snap(), state,
            hash_map={"tokC": h_new, "tokN": h_new, "tokD": sha("# md")},
            revision_map={"tokD": "42"},
            cache_dir=self.cache.path, last_downloaded_at=NOW,
        )
        nodes = merged["nodes"]
        # changed：新 edit_time + 新 hash + 新时间戳
        self.assertEqual(nodes["tokC"]["edit_time_ms"], EDIT + 5000)
        self.assertEqual(nodes["tokC"]["feishu_hash"], h_new)
        self.assertEqual(nodes["tokC"]["last_downloaded_at"], NOW)
        # unchanged：原条目逐字段保留
        self.assertEqual(nodes["tokU"], state["nodes"]["tokU"])
        # deleted：移除
        self.assertNotIn("tokX", nodes)
        # new + hash：新增条目，cache_file 按 token+ext 命名
        self.assertEqual(nodes["tokN"]["feishu_hash"], h_new)
        self.assertEqual(nodes["tokN"]["cache_file"], "feishu_sync_cache/tokN.docx")
        # new 无 hash（下载失败）：省略，下次同步重试
        self.assertNotIn("tokF", nodes)
        # doc 型：revision 来自 revision_map，hash 为 fetch 文本哈希
        self.assertEqual(nodes["tokD"]["feishu_revision_id"], "42")
        self.assertEqual(nodes["tokD"]["cache_file"], "feishu_sync_cache/tokD.md")
        # 元信息
        self.assertEqual(merged["_last_sync_at"], NOW)
        self.assertEqual(merged["_kb_url"], "kb")

    def test_merge_failed_doc_keeps_old_entry_for_retry(self):
        state = self._state(cache_data=b"old")
        self.cache.write("tokU.docx", b"old")
        merged = cd.merge_state(
            self._snap(), state,
            hash_map={}, revision_map={},  # 全部失败
            cache_dir=self.cache.path, last_downloaded_at=NOW,
        )
        nodes = merged["nodes"]
        # changed 的 tokC 下载失败：保留旧条目（旧 edit_time），下次同步重新比对为 changed
        self.assertEqual(nodes["tokC"]["edit_time_ms"], EDIT)
        self.assertEqual(nodes["tokC"]["feishu_hash"], sha(b"old"))
        # unchanged 的 tokU 保留
        self.assertEqual(nodes["tokU"], state["nodes"]["tokU"])

    def test_merge_first_run_bootstrap(self):
        snap = {"_kb_url": "kb", "_space_id": "sp", "nodes": {
            "tokA": snap_node("tokA"), "tokB": snap_node("tokB", obj_type="docx", ext=".md"),
        }}
        h = sha(b"bytes")
        merged = cd.merge_state(
            snap, None,
            hash_map={"tokA": h, "tokB": sha("# md")},
            revision_map={"tokB": "7"},
            cache_dir=self.cache.path, last_downloaded_at=NOW,
        )
        self.assertEqual(set(merged["nodes"].keys()), {"tokA", "tokB"})
        self.assertEqual(merged["nodes"]["tokB"]["feishu_revision_id"], "7")


class TestWriteAtomic(unittest.TestCase):

    def test_write_atomic_round_trip_and_no_tmp_leftover(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            obj = {"_version": 1, "nodes": {"tokA": {"title": "中文标题"}}}
            cd.write_atomic(path, obj)
            with open(path, "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f), obj)
            self.assertFalse(os.path.exists(path + ".tmp"))


class TestMain(unittest.TestCase):

    def test_plan_mode_writes_plan_file_and_prints_delta(self):
        with tempfile.TemporaryDirectory() as d:
            snap_path = os.path.join(d, "snap.json")
            state_path = os.path.join(d, "state.json")
            plan_path = os.path.join(d, "plan.json")
            with open(snap_path, "w", encoding="utf-8") as f:
                json.dump({"nodes": {"tokA": snap_node("tokA")}}, f)
            # state 缺失 → first_run
            rc = cd.main(["--nodes", snap_path, "--state", state_path, "--out", plan_path])
            self.assertEqual(rc, 0)
            with open(plan_path, "r", encoding="utf-8") as f:
                plan = json.load(f)
            self.assertTrue(plan["first_run"])
            self.assertEqual(plan["verdicts"]["tokA"]["verdict"], "first_run")


class TestOnlySubset(unittest.TestCase):
    """--only（定点重摄）语义：子集判定 / 禁 deleted 推断 / 状态保留未触碰 token。"""

    def setUp(self):
        self.cache = CacheDir()

    def tearDown(self):
        self.cache.close()

    def _snap3(self):
        return {"_kb_url": "x", "_space_id": "s",
                "nodes": {f"tok{i}": snap_node(f"tok{i}") for i in "ABC"}}

    def _state3(self):
        return {"_version": 1, "_kb_url": "x", "_space_id": "s",
                "nodes": {f"tok{i}": state_entry(f"tok{i}", f"tok{i}.docx")
                          for i in "ABC"}}

    def test_plan_only_restricts_verdicts_and_summary(self):
        snap = self._snap3()
        state = self._state3()
        result = cd.classify(snap, state, self.cache.path, only=["tokA"])
        self.assertEqual(set(result["verdicts"].keys()), {"tokA"})
        self.assertEqual(result["summary"]["total"], 1)
        self.assertNotIn("tokB", result["verdicts"])
        self.assertNotIn("tokC", result["verdicts"])

    def test_plan_only_subset_snapshot_never_marks_deleted(self):
        # 定点重摄的子集快照只含 tokA；无 --only 时 tokB/tokC 会被判 deleted
        snap = {"nodes": {"tokA": snap_node("tokA")}}
        state = self._state3()
        result = cd.classify(snap, state, self.cache.path, only=["tokA"])
        self.assertEqual(list(result["verdicts"].keys()), ["tokA"])
        self.assertNotIn("deleted", [v["verdict"] for v in result["verdicts"].values()])

    def test_merge_only_preserves_untouched_tokens(self):
        data = b"new bytes"
        h = sha(data)
        self.cache.write("tokA.docx", data)
        snap = {"nodes": {"tokA": snap_node("tokA", edit_time_ms=EDIT + 1)}}
        state = self._state3()
        merged = cd.merge_state(snap, state, {"tokA": h}, {}, self.cache.path,
                                NOW, only=["tokA"])
        self.assertEqual(set(merged["nodes"].keys()), {"tokA", "tokB", "tokC"})
        self.assertEqual(merged["nodes"]["tokA"]["feishu_hash"], h)
        # 未触碰 token 原样保留（含旧字段）
        self.assertEqual(merged["nodes"]["tokB"], state["nodes"]["tokB"])
        self.assertEqual(merged["nodes"]["tokC"], state["nodes"]["tokC"])

    def test_only_unknown_token_warns_and_continues(self):
        snap = self._snap3()
        state = self._state3()
        result = cd.classify(snap, state, self.cache.path, only=["tokA", "tokZ"])
        self.assertEqual(set(result["verdicts"].keys()), {"tokA"})
        self.assertTrue(result.get("warnings"))
        self.assertIn("tokZ", result["warnings"][0])

    def test_only_empty_intersection_raises(self):
        snap = self._snap3()
        state = self._state3()
        with self.assertRaises(ValueError):
            cd.classify(snap, state, self.cache.path, only=["tokZ"])


class TestColdCacheReplan(unittest.TestCase):
    """契约：Step 3.9 冷缓存重跑 plan（spec 2026-09-11 §7）。

    B 采纳云端 state 后本地缓存为空 → plan 判 changed → Step 3 全量下载
    （内容写入缓存）→ 重跑同一条 plan → 刚下载的缓存通过哈希校验 →
    翻转 unchanged。check_delta 零改动，本测试锚定该语义防回归。
    """

    def setUp(self):
        self.cache = CacheDir()

    def tearDown(self):
        self.cache.close()

    def test_cold_cache_flips_to_unchanged_after_download(self):
        data = b"downloaded content"
        h = sha(data)
        snap = {"nodes": {"tokA": snap_node("tokA")}}
        state = {"_version": 1,
                 "nodes": {"tokA": state_entry("tokA", "tokA.docx", feishu_hash=h)}}
        # 冷缓存（无缓存文件）→ 第一轮 plan 判 changed
        r1 = cd.classify(snap, state, self.cache.path)
        self.assertEqual(r1["verdicts"]["tokA"]["verdict"], "changed")
        # Step 3 全量下载：内容写入缓存 → 重跑 plan → 翻转 unchanged
        self.cache.write("tokA.docx", data)
        r2 = cd.classify(snap, state, self.cache.path)
        self.assertEqual(r2["verdicts"]["tokA"]["verdict"], "unchanged")
        self.assertEqual(r2["summary"]["skip"], 1)

    def test_changed_content_stays_changed_after_replan(self):
        snap = {"nodes": {"tokA": snap_node("tokA")}}
        state = {"_version": 1,
                 "nodes": {"tokA": state_entry("tokA", "tokA.docx",
                                               feishu_hash=sha(b"old"))}}
        r1 = cd.classify(snap, state, self.cache.path)
        self.assertEqual(r1["verdicts"]["tokA"]["verdict"], "changed")
        # 下载内容与状态哈希不符（真变化）→ 重跑保持 changed
        self.cache.write("tokA.docx", b"new content")
        r2 = cd.classify(snap, state, self.cache.path)
        self.assertEqual(r2["verdicts"]["tokA"]["verdict"], "changed")

    def test_doc_type_content_drift_stays_changed(self):
        md_old = "# old text"
        md_new = "# new text"
        snap = {"nodes": {"tokD": snap_node("tokD", title="原生文档",
                                            obj_type="docx", ext=".md")}}
        state = {"_version": 1,
                 "nodes": {"tokD": state_entry("tokD", "原生文档", obj_type="docx",
                                               ext=".md", feishu_hash=sha(md_old),
                                               revision_id="80")}}
        # 下载得到新文本（哈希与状态不符）→ 判定保持 changed
        self.cache.write("tokD.md", md_new)
        r2 = cd.classify(snap, state, self.cache.path)
        self.assertEqual(r2["verdicts"]["tokD"]["verdict"], "changed")


if __name__ == "__main__":
    unittest.main()