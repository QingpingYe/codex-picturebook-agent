#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wiki-ingest「外链扫描与分流」— 外链扫描机械层（零网络、零 LLM）。

对提取后文本跑三条正则 → 归一化去重 → 四层过滤中的 1/2/4 层（硬排除 /
廉价代理打分 / 上限裁剪）→ 分流 → 输出候选清单与 SCAN_DONE 证据行。
LLM 仅承担第三层语义判定（`pending_llm[]`）与门禁交互。
协议出处：references/feishu-wiki-extraction.md「外链扫描与归一化」「外链过滤」。
"""

import argparse
import json
import os
import re
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^\s)]+)\)")
ANGLE = re.compile(r"<(https?://[^>]+)>")
BARE = re.compile(r"""(?<![(\]<])https?://[^\s<>"'）】」，。；、]+""")
ASCII_TRAILING = ".,;:!?)]}\"'"
CONTEXT_HALF = 120


def _host_in(host, domains):
    """域名匹配含子域：host == d 或 host 以 ".d" 结尾。"""
    host = (host or "").lower()
    return any(host == d or host.endswith("." + d) for d in (domains or []))


def _strip_trailing(raw):
    return raw.rstrip(ASCII_TRAILING)


def extract_urls(text, host_doc_id):
    """三条正则提取 URL；裸 URL 尾部标点剥离；记录 offset/context/host。"""
    hits = []

    def add(url, anchor, start, end):
        hits.append({
            "url": _strip_trailing(url),
            "anchor_text": anchor,
            "char_offset": start,
            "context_window": text[max(0, start - CONTEXT_HALF):
                                   min(len(text), end + CONTEXT_HALF)],
            "host_doc_id": host_doc_id,
        })

    for m in MD_LINK.finditer(text):
        add(m.group(2), m.group(1), m.start(), m.end())
    for m in ANGLE.finditer(text):
        add(m.group(1), "", m.start(), m.end())
    for m in BARE.finditer(text):
        add(m.group(0), "", m.start(), m.end())
    return hits


def normalize_url(url, tracking_params):
    """去重键：scheme/host 小写、去默认端口、去 hash、剔跟踪参数、query 排序、
    path 小写去尾斜杠、百分号编码大写。"""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    if port is not None:
        host = f"{host}:{port}"
    path = (parts.path or "").lower()
    if len(path) > 1:
        path = path.rstrip("/")
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k not in (tracking_params or [])]
    query = urlencode(sorted(kept))
    path = re.sub(r"%[0-9a-fA-F]{2}", lambda m: m.group(0).upper(), path)
    return urlunsplit((scheme, host, path, query, ""))


def hard_exclude(url, ext, kb_url):
    """第一层硬排除：scheme / denyDomains / denyExtensions / KB URL 自身。"""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        return True, "scheme"
    host = (parts.hostname or "").lower()
    if _host_in(host, ext.get("denyDomains")):
        return True, "denyDomains"
    path = (parts.path or "").lower()
    for suffix in (ext.get("denyExtensions") or []):
        if path.endswith(suffix.lower()):
            return True, "denyExtensions"
    if kb_url and normalize_url(url, ext.get("trackingParams")) == \
            normalize_url(kb_url, ext.get("trackingParams")):
        return True, "kb_url_itself"
    return False, ""


def cheap_score(hit, ext):
    """第二层廉价代理打分：五信号任一命中即保留；零信号纯域名根丢弃。"""
    url = hit.get("url", "")
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path or ""
    blob = (path + " " + parts.query + " " + (hit.get("anchor_text") or "")).lower()
    ctx = (hit.get("context_window") or "").lower()
    signals = 0
    reasons = []
    if _host_in(host, ext.get("allowDomains")):
        signals += 1
        reasons.append("allowDomains")
    if any(seg for seg in path.split("/")):
        signals += 1
        reasons.append("path_depth")
    if any(kw.lower() in blob for kw in (ext.get("substanceKeywords") or [])):
        signals += 1
        reasons.append("substance")
    if any(kw.lower() in ctx for kw in (ext.get("citationIntentKeywords") or [])):
        signals += 1
        reasons.append("citation")
    if _host_in(host, ext.get("feishuDomains")) and any(
            path.startswith(p) for p in (ext.get("feishuDocPathPrefixes") or [])):
        signals += 1
        reasons.append("feishu_doc")
    keep = signals >= 1
    return keep, signals, ("zero-signal bare root" if not keep
                           else ",".join(reasons))


def kb_internal(url, known_tokens, feishu_domains=None):
    """URL 命中 kb_node_index 中任一 node_token / obj_token 且宿主为飞书域 →
    知识库内部链接（转交叉引用）。非飞书域上的同形 token 属巧合，不判内部。"""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if feishu_domains is not None and not _host_in(host, feishu_domains):
        return False
    return any(tok and tok in url for tok in known_tokens)


def route_channel(url, ext):
    """分流：feishu 域 + 文档路径前缀 → feishu；feishu 域非文档路径 → skip；其余 web。"""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path or ""
    if _host_in(host, ext.get("feishuDomains")):
        if any(path.startswith(p) for p in (ext.get("feishuNonDocPathPrefixes") or [])):
            return "skip"
        if any(path.startswith(p) for p in (ext.get("feishuDocPathPrefixes") or [])):
            return "feishu"
        return "skip"
    return "web"


def dedupe_global(hits, tracking_params=None):
    """去重键 = 归一化 URL；去重范围 = 本次同步全局；重复命中记入 referenced_by。"""
    uniques = {}
    for h in hits:
        key = normalize_url(h["url"], tracking_params)
        if key in uniques:
            doc = h["host_doc_id"]
            if doc not in uniques[key]["referenced_by"]:
                uniques[key]["referenced_by"].append(doc)
        else:
            entry = dict(h)
            entry["referenced_by"] = [h["host_doc_id"]]
            uniques[key] = entry
    return list(uniques.values())


def rank_and_cap(candidates, cfg):
    """第四层上限裁剪。排序键：allowDomains > 正信号数 > 出现次数 > 首次位置。"""
    ordered = sorted(candidates, key=lambda c: (
        -(1 if c.get("allow") else 0),
        -int(c.get("signals") or 0),
        -int(c.get("occurrences") or 0),
        int(c.get("first_pos") or 0),
    ))
    per_doc = {}
    kept = []
    for c in ordered:
        doc = c.get("host_doc_id") or ""
        if per_doc.get(doc, 0) >= int(cfg.get("maxUrlsPerDoc") or 0):
            continue
        if len(kept) >= int(cfg.get("maxUrlsPerSync") or 0):
            break
        kept.append(c)
        per_doc[doc] = per_doc.get(doc, 0) + 1
    return kept


def main(argv):
    ap = argparse.ArgumentParser(description="外链扫描机械层（零网络、零 LLM）")
    ap.add_argument("--texts-dir", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        with open(args.settings, encoding="utf-8") as f:
            settings = json.load(f)
        ext = settings.get("externalLinks") or {}
        kb_url = (settings.get("feishuKnowledgeBase") or {}).get("url", "")
        with open(args.meta, encoding="utf-8") as f:
            meta = json.load(f)
        with open(args.snapshot, encoding="utf-8") as f:
            snap = json.load(f)
    except Exception as e:
        print(f"ERROR: scan_external_links input load failed: {e}", file=sys.stderr)
        return 1
    if not os.path.isdir(args.texts_dir):
        print("ERROR: texts-dir not found: %s" % args.texts_dir, file=sys.stderr)
        return 1

    known_tokens = set(snap.get("nodes") or {})
    known_tokens.update(
        n.get("obj_token") for n in (snap.get("nodes") or {}).values()
        if n.get("obj_token"))

    raw = []
    for fn in sorted(os.listdir(args.texts_dir)):
        if not fn.endswith(".md"):
            continue
        with open(os.path.join(args.texts_dir, fn), encoding="utf-8") as f:
            raw.extend(extract_urls(f.read(), fn[:-3]))

    n_raw = len(raw)
    uniques = dedupe_global(raw, ext.get("trackingParams"))
    n_unique = len(uniques)
    for u in uniques:
        key = normalize_url(u["url"], ext.get("trackingParams"))
        u["occurrences"] = sum(
            1 for r in raw
            if normalize_url(r["url"], ext.get("trackingParams")) == key)

    candidates = []
    records = []
    for u in uniques:
        rec = dict(u)
        rec["host_title"] = (meta.get(u["host_doc_id"]) or {}).get("title", "")
        if kb_internal(u["url"], known_tokens, ext.get("feishuDomains")):
            rec["disposition"] = "kb_internal"
            records.append(rec)
            continue
        excluded, reason = hard_exclude(u["url"], ext, kb_url)
        if excluded:
            rec["disposition"] = "hard_excluded"
            rec["reason"] = reason
            records.append(rec)
            continue
        keep, signals, reason = cheap_score(u, ext)
        if not keep:
            rec["disposition"] = "proxy_dropped"
            rec["reason"] = reason
            records.append(rec)
            continue
        rec["signals"] = signals
        rec["allow"] = _host_in(
            (urlsplit(u["url"]).hostname or "").lower(),
            ext.get("allowDomains"))
        rec["first_pos"] = u.get("char_offset", 0)
        rec["channel"] = route_channel(u["url"], ext)
        candidates.append(rec)
        records.append(rec)

    kept = rank_and_cap(candidates, ext)
    kept_ids = {id(c) for c in kept}
    for c in candidates:
        if id(c) not in kept_ids:
            c["disposition"] = "capped"
    f_count = sum(1 for c in kept if c.get("channel") == "feishu")
    w_count = sum(1 for c in kept if c.get("channel") == "web")

    result = {
        "raw": n_raw,
        "unique": n_unique,
        "candidates": len(kept),
        "feishu": f_count,
        "web": w_count,
        "pending_llm": [dict(c) for c in kept],
        "records": records,
    }
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, args.out)

    line = (f"SCAN_DONE: {n_raw} raw → {n_unique} unique → "
            f"{len(kept)} candidates → {f_count} feishu + {w_count} web")
    if n_raw == 0:
        line += "（已验证零 URL）"
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
