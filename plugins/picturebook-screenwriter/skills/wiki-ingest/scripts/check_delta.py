#!/usr/bin/env python3
"""wiki-ingest v9 — 飞书知识库增量比对脚本（check_delta）

纯函数、无网络、不调 lark-cli、不查资料库。LLM 代理负责 lark-cli 调用与
网络/DB 操作，本脚本只做确定性的「节点快照 × 持久状态」比对与状态合并。

两个阶段：
  plan 阶段：
    python check_delta.py --nodes <snapshot.json> --state <state.json>
      [--cache-dir <dir>] [--force-full] [--out <plan.json>]
    → 逐节点 verdict + DELTA 摘要行（stdout）
  finalize 阶段：
    python check_delta.py --nodes <snapshot.json> --state <state.json>
      [--cache-dir <dir>] --hash-map <hash_map.json>
      --revision-map <revision_map.json> --write-state <state.json>
    → 原子合并写回状态（仅回填成功摄入的文档；失败节点保留旧条目下次重试）

verdict 枚举：first_run / new / changed / unchanged / deleted / unknown
判定规则表见 skills/wiki-ingest/SKILL.md 的「增量判定」——本脚本为唯一权威实现，
文档描述与本脚本不符时以本脚本为准（有 fixture 单测兜底：
python test_check_delta.py）。

fail-safe 原则：跳过必须有正向证据（edit_time 相等 + 缓存哈希校验通过），
证据缺失一律 unknown/changed → 照常下载，绝不漏同步。

feishu_hash 语义：
  - file 型 = sha256(下载的原始文件字节)
  - docx/wiki 型 = sha256(docs +fetch 返回的 Markdown 文本 UTF-8 字节)
与 feishu_sync_cache/ 中的缓存文件同源，可互相校验。
"""

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))

DOC_TYPES = ("docx", "wiki")


def load_json(path):
    """文件不存在返回 None；解析失败抛 ValueError（由 main fail-safe 捕获）。"""
    if not path or not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_atomic(path, obj):
    """temp + os.replace 原子写，UTF-8、ensure_ascii=False（中文标题原样保留）。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _cache_name(token, node):
    ext = node.get("ext") or (".md" if node.get("obj_type") in DOC_TYPES else ".bin")
    if not ext.startswith("."):
        ext = "." + ext
    return f"feishu_sync_cache/{token}{ext}"


def _verify_cache(old_entry, cache_dir):
    """校验状态条目对应的缓存文件存在且 sha256 与 feishu_hash 一致。"""
    if not cache_dir:
        return "no_cache_dir"
    name = os.path.basename(old_entry.get("cache_file") or "")
    if not name:
        return "cache_file_unknown"
    path = os.path.join(cache_dir, name)
    if not os.path.isfile(path):
        return "cache_missing"
    expected = old_entry.get("feishu_hash")
    if not expected:
        return "hash_missing"
    with open(path, "rb") as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    return "ok" if actual == expected else "hash_mismatch"


def _classify_node(token, node, old, cache_dir, force_full):
    if not isinstance(node, dict):
        return {"verdict": "unknown", "reason": "schema error in snapshot node"}
    if old is None:
        return {"verdict": "new", "reason": "not in state"}
    if force_full:
        return {"verdict": "changed", "reason": "force_full"}
    new_et = node.get("edit_time_ms")
    old_et = old.get("edit_time_ms")
    if new_et is None:
        return {"verdict": "unknown",
                "reason": "edit_time_ms missing (degraded chain failed)"}
    if old_et is None or new_et != old_et:
        return {"verdict": "changed", "reason": "edit_time changed"}
    if node.get("title") != old.get("title"):
        return {"verdict": "changed", "reason": "title changed (rename)"}
    obj_type = node.get("obj_type") or old.get("obj_type")
    if obj_type in DOC_TYPES and old.get("feishu_revision_id") == "N/A":
        return {"verdict": "changed", "reason": "revision_id is N/A (repair legacy row)"}
    check = _verify_cache(old, cache_dir)
    if check == "ok":
        return {"verdict": "unchanged", "reason": "edit_time equal and cache hash verified"}
    return {"verdict": "changed", "reason": f"cache check failed: {check}"}


def _summarize(verdicts, first_run):
    counts = defaultdict(int)
    for v in verdicts.values():
        counts[v["verdict"]] += 1
    new = counts["first_run"] + counts["new"]
    changed = counts["changed"]
    unknown = counts["unknown"]
    return {
        "total": len(verdicts),
        "skip": counts["unchanged"],
        "process": new + changed + unknown,
        "new": new,
        "changed": changed,
        "deleted": counts["deleted"],
        "unknown": unknown,
        "first_run": first_run,
    }


def build_summary_line(summary):
    line = ("DELTA: {total} nodes → skip {skip} / process {process} / new {new} "
            "/ deleted {deleted} / unknown {unknown}").format(**summary)
    if summary.get("first_run"):
        line += " / first_run"
    return line


def classify(snapshot, state, cache_dir=None, force_full=False, only=None):
    """plan 阶段核心：逐节点判定 verdict。

    返回 {"first_run": bool, "verdicts": {token: {"verdict", "reason"}}, "summary": {...}}
    verdicts 包含 deleted 节点（state 有、快照无），供代理做 node-get 删除复核。

    only（定点重摄）：仅判定指定 token 子集；**禁用 deleted 推断**（子集快照
    不得把范围外 token 误判删除）；未命中快照的 token 记入 warnings；
    与快照交集为空抛 ValueError（fail-safe）。
    """
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("nodes"), dict):
        raise ValueError("snapshot malformed: nodes must be a dict")
    warnings = []
    if only is not None:
        only_set = set(only)
        unknown = sorted(only_set - set(snapshot["nodes"].keys()))
        if unknown:
            warnings.append(f"--only 未命中快照的 token: {unknown}")
        if not only_set & set(snapshot["nodes"].keys()):
            raise ValueError("--only 与快照无交集")
    if not state or not state.get("nodes"):
        tokens = snapshot["nodes"]
        if only is not None:
            only_set = set(only)
            tokens = {t: snapshot["nodes"][t]
                      for t in only_set & set(snapshot["nodes"].keys())}
        verdicts = {t: {"verdict": "first_run", "reason": "state missing (bootstrap)"}
                    for t in tokens}
        result = {"first_run": True, "verdicts": verdicts,
                  "summary": _summarize(verdicts, True)}
        if warnings:
            result["warnings"] = warnings
        return result

    old_nodes = state.get("nodes") or {}
    verdicts = {}
    tokens = snapshot["nodes"]
    if only is not None:
        only_set = set(only)
        tokens = {t: snapshot["nodes"][t]
                  for t in only_set & set(snapshot["nodes"].keys())}
    for token, node in tokens.items():
        verdicts[token] = _classify_node(token, node, old_nodes.get(token),
                                         cache_dir, force_full)
    if only is None:
        for token in old_nodes:
            if token not in snapshot["nodes"]:
                verdicts[token] = {"verdict": "deleted",
                                   "reason": "in state but not in snapshot"}
    result = {"first_run": False, "verdicts": verdicts,
              "summary": _summarize(verdicts, False)}
    if warnings:
        result["warnings"] = warnings
    return result


def merge_state(snapshot, old_state, hash_map, revision_map, cache_dir,
                last_downloaded_at, only=None):
    """finalize 阶段核心：合并快照与新旧数据，产出新状态（不落盘）。

    - unchanged → 保留旧条目（逐字段不动）
    - new/changed/unknown → 需要 hash（file 型）或 hash+revision（docx/wiki 型）
      作为"成功摄入"证据才更新条目；证据缺失 → 保留旧条目（下次重试），
      新节点则省略（下次仍判 new）
    - deleted（state 有、快照无）→ 移除

    only（定点重摄）：仅更新指定 token 子集，**其余 token 旧条目原样保留**
    （禁用 deleted 移除语义）。
    """
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("nodes"), dict):
        raise ValueError("snapshot malformed: nodes must be a dict")
    old_nodes = (old_state or {}).get("nodes") or {}
    old_meta = old_state or {}
    merged = {
        "_version": 1,
        "_kb_url": snapshot.get("_kb_url") or old_meta.get("_kb_url"),
        "_space_id": snapshot.get("_space_id") or old_meta.get("_space_id"),
        "_last_sync_at": last_downloaded_at,
        "nodes": {},
    }
    verdicts = classify(snapshot, old_state, cache_dir, only=only)["verdicts"]
    hash_map = hash_map or {}
    revision_map = revision_map or {}
    if only is not None:
        merged["nodes"] = dict(old_nodes)
    for token, node in snapshot["nodes"].items():
        if not isinstance(node, dict):
            if token in old_nodes:
                merged["nodes"][token] = old_nodes[token]
            continue
        if verdicts.get(token, {}).get("verdict") == "unchanged":
            if token in old_nodes:
                merged["nodes"][token] = old_nodes[token]
            continue
        obj_type = node.get("obj_type") or "file"
        h = hash_map.get(token)
        if not h:
            if token in old_nodes:
                merged["nodes"][token] = old_nodes[token]
            continue
        if obj_type in DOC_TYPES and revision_map.get(token) is None:
            if token in old_nodes:
                merged["nodes"][token] = old_nodes[token]
            continue
        entry = dict(node)
        entry["feishu_hash"] = h
        entry["feishu_revision_id"] = (revision_map.get(token)
                                       if obj_type in DOC_TYPES else None)
        entry["cache_file"] = _cache_name(token, node)
        entry["last_downloaded_at"] = last_downloaded_at
        merged["nodes"][token] = entry
    return merged


def main(argv=None):
    parser = argparse.ArgumentParser(description="Feishu KB 增量比对（无网络纯函数）")
    parser.add_argument("--nodes", required=True, help="节点快照 JSON（代理产自 node-get）")
    parser.add_argument("--state", required=True, help="持久状态 JSON（不存在则 bootstrap）")
    parser.add_argument("--cache-dir", default=None, help="feishu_sync_cache 目录（缓存哈希校验）")
    parser.add_argument("--force-full", action="store_true",
                        help="逃生舱：全部判 changed（对应 forceFullSync 配置）")
    parser.add_argument("--out", help="plan 阶段：verdict 计划输出路径")
    parser.add_argument("--hash-map", help="finalize 阶段：{node_token: sha256}")
    parser.add_argument("--revision-map", help="finalize 阶段：{node_token: revision_id}")
    parser.add_argument("--write-state", help="finalize 阶段：状态落盘路径（原子写）")
    parser.add_argument("--only", default=None,
                        help="定点重摄：逗号分隔的 node_token 子集，只判定/合并这些 token；"
                             "禁 deleted 推断、finalize 保留其余 token")
    args = parser.parse_args(argv)

    only = None
    if args.only:
        only = [t.strip() for t in args.only.split(",") if t.strip()]

    try:
        snap = load_json(args.nodes)
        if not isinstance(snap, dict) or not isinstance(snap.get("nodes"), dict):
            raise ValueError("snapshot malformed: nodes must be a dict")
        state = load_json(args.state)
        if args.write_state:
            merged = merge_state(snap, state, load_json(args.hash_map),
                                 load_json(args.revision_map), args.cache_dir,
                                 datetime.now(CST).isoformat(), only=only)
            write_atomic(args.write_state, merged)
            line = build_summary_line(classify(snap, state, args.cache_dir,
                                               only=only)["summary"])
            print(line)
            return 0
        result = classify(snap, state, args.cache_dir, args.force_full, only=only)
        if args.out:
            write_atomic(args.out, result)
        print(build_summary_line(result["summary"]))
        for w in result.get("warnings") or []:
            print(f"WARN: {w}", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"ERROR: check_delta failed: {e}", file=sys.stderr)
        print("DELTA: 0 nodes → skip 0 / process 0 / new 0 / deleted 0 / unknown 0")
        print("FALLBACK: 全部节点按 unknown 处理（等同旧全量下载），不阻断同步",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
