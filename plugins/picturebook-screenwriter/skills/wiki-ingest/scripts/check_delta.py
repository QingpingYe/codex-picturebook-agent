#!/usr/bin/env python3
"""飞书知识库增量比对：远端 source baseline × 本轮节点快照。

plan 阶段：
    python check_delta.py --nodes <snapshot.json> --source-baseline <baseline.json>
      [--force-full] [--only <tokens>] [--out <plan.json>]

本脚本不读取跨运行本地 state，不写入远端。source baseline 由
feishu-knowledge-store 的只读 source-baseline 命令生成；缺失或证据不完整时
采用 fail-safe：重新读取或停止，不得漏掉变化源。
"""

import argparse
import json
import os
import sys
from collections import defaultdict

DOC_TYPES = ("docx", "wiki")
VALID_VERDICTS = ("first_run", "new", "changed", "unchanged", "deleted", "unknown")


def load_json(path):
    """文件不存在返回 None；解析失败抛 ValueError。"""
    if not path or not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_atomic(path, obj):
    """temp + os.replace 原子写，UTF-8。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_source_baseline(path):
    """读取远端索引投影；路径不存在表示首次运行。"""
    payload = load_json(path)
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError("source baseline must be an object")
    if payload.get("schema_version") != 1 or not isinstance(payload.get("entries"), list):
        raise ValueError("source baseline has invalid schema")
    return payload


def _valid_time(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def build_token_baseline(payload):
    """把远端索引条目收束为 token -> {revisions, edit_times, complete}。"""
    if not payload:
        return {}
    evidence = {}
    for raw in payload.get("entries") or []:
        if not isinstance(raw, dict):
            raise ValueError("source baseline entry must be an object")
        revisions = raw.get("source_revisions")
        if not isinstance(revisions, dict) or not revisions:
            raise ValueError("source baseline entry missing source_revisions")
        times = raw.get("source_edit_times")
        for token, revision in revisions.items():
            if not isinstance(token, str) or not token:
                raise ValueError("source baseline token is invalid")
            item = evidence.setdefault(token, {
                "revisions": set(),
                "edit_times": set(),
                "missing_revision": False,
                "missing_time": False,
            })
            if isinstance(revision, str) and revision.strip():
                item["revisions"].add(revision)
            else:
                item["missing_revision"] = True
            if not isinstance(times, dict) or token not in times or not _valid_time(times[token]):
                item["missing_time"] = True
            else:
                item["edit_times"].add(times[token])

    result = {}
    for token, item in evidence.items():
        revisions = sorted(item["revisions"])
        edit_times = sorted(item["edit_times"])
        complete = (
            not item["missing_revision"]
            and not item["missing_time"]
            and len(revisions) == 1
            and len(edit_times) == 1
        )
        result[token] = {
            "revisions": revisions,
            "edit_times": edit_times,
            "complete": complete,
        }
    return result


def _classify_node(token, node, baseline, force_full):
    if not isinstance(node, dict):
        return {"verdict": "unknown", "reason": "schema error in snapshot node"}
    if baseline is None:
        return {"verdict": "new", "reason": "not in remote source baseline"}
    if force_full:
        return {"verdict": "changed", "reason": "force_full"}

    edit_time = node.get("edit_time_ms")
    if not _valid_time(edit_time):
        return {
            "verdict": "unknown",
            "reason": "edit_time_ms missing or invalid in snapshot",
        }
    if not baseline.get("complete"):
        return {
            "verdict": "changed",
            "reason": "remote source evidence incomplete or conflicting",
        }
    if edit_time != baseline["edit_times"][0]:
        return {"verdict": "changed", "reason": "edit_time changed"}

    obj_type = node.get("obj_type") or "file"
    if obj_type in DOC_TYPES:
        revision = node.get("revision_id")
        if not isinstance(revision, str) or not revision or revision == "N/A":
            return {"verdict": "changed", "reason": "revision_id missing or N/A"}
        if revision != baseline["revisions"][0]:
            return {"verdict": "changed", "reason": "revision changed"}
    return {
        "verdict": "unchanged",
        "reason": "remote edit_time and applicable revision match",
    }


def _summarize(verdicts, first_run):
    counts = defaultdict(int)
    for value in verdicts.values():
        counts[value["verdict"]] += 1
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


def classify(snapshot, source_baseline, cache_dir=None, force_full=False, only=None):
    """按远端 source baseline 判定快照节点；cache_dir 保留为兼容参数但不决定 verdict。"""
    del cache_dir
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

    baseline = build_token_baseline(source_baseline)
    first_run = not baseline
    verdicts = {}
    tokens = snapshot["nodes"]
    if only is not None:
        only_set = set(only)
        tokens = {t: snapshot["nodes"][t] for t in only_set & set(snapshot["nodes"].keys())}

    for token, node in tokens.items():
        if first_run:
            verdicts[token] = {
                "verdict": "first_run",
                "reason": "source baseline missing or empty (bootstrap)",
            }
        else:
            verdicts[token] = _classify_node(token, node, baseline.get(token),
                                             force_full)
    if only is None and not first_run:
        for token in baseline:
            if token not in snapshot["nodes"]:
                verdicts[token] = {
                    "verdict": "deleted",
                    "reason": "in remote index but not in snapshot",
                }

    result = {
        "first_run": first_run,
        "verdicts": verdicts,
        "summary": _summarize(verdicts, first_run),
    }
    if warnings:
        result["warnings"] = warnings
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Feishu KB 增量比对（远端 source baseline）")
    parser.add_argument("--nodes", required=True, help="节点快照 JSON")
    parser.add_argument("--source-baseline", help="只读 source baseline JSON；缺失表示首次运行")
    parser.add_argument("--cache-dir", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--force-full", action="store_true",
                        help="调度方显式传 --force-full 时全部判 changed")
    parser.add_argument("--out", help="verdict 计划输出路径")
    parser.add_argument("--only", default=None,
                        help="逗号分隔的 node_token 子集；不推断 deleted")
    args = parser.parse_args(argv)

    only = None
    if args.only:
        only = [token.strip() for token in args.only.split(",") if token.strip()]

    try:
        snapshot = load_json(args.nodes)
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("nodes"), dict):
            raise ValueError("snapshot malformed: nodes must be a dict")
        source_baseline = load_source_baseline(args.source_baseline)
        result = classify(snapshot, source_baseline, args.cache_dir,
                          args.force_full, only=only)
        if args.out:
            write_atomic(args.out, result)
        print(build_summary_line(result["summary"]))
        for warning in result.get("warnings") or []:
            print(f"WARN: {warning}", file=sys.stderr)
        return 0
    except Exception as error:
        print(f"ERROR: check_delta failed: {error}", file=sys.stderr)
        print("DELTA: 0 nodes → skip 0 / process 0 / new 0 / deleted 0 / unknown 0")
        print("FALLBACK: 全部节点按 unknown 处理（等同旧全量下载），不阻断同步",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())