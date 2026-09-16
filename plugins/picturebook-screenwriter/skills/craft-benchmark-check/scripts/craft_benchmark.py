#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
craft_benchmark.py — 绘本文字工艺量化自测

把《量化基准表》从散文基准变成可执行的确定型检查：解析逐页脚本，计算十五项工艺指标，
逐项对标体裁级通用基准，产出报告。

设计原则：
- 阈值来源单一权威：优先解析 skills/text-craft/references/craft-baselines.md 的机读基准块，
  失败时回退本文件内置的同源默认值（避免双份维护，AB 两处不同步）。
- 零假阴性取向：指标计算尽量走确定性正则 / 计数，不做语义判断（语义判定归 LLM-judge）。
- 只报告不修改。
- 不依赖第三方库，纯标准库。

用法：
    python craft_benchmark.py <script.md> [--genre 韵文冒险] [--age 4-6]
                              [--baselines <craft-baselines.md>] [--json <out.json>]
                              [--climax-pages 11,12] [--title-word <书名关键词>]

判定口径（与 craft-baselines.md 的机读基准块一致）：
    指标落在区间内            → PASS
    越界但在容忍度内          → WARN
    越界超过容忍度            → FAIL
退出码：有 FAIL → 1；否则 0。
"""
import argparse
import json
import os
import re
import sys
from collections import Counter

TOLERANCE_PCT = 20  # 与机读基准块的 tolerance_pct 一致；被基准块覆盖

# ---------------------------------------------------------------------------
# 内置兜底基准（与 text-craft/references/craft-baselines.md 的机读基准块同源）
# 仅在无法从基准文件解析时使用。修改基准请改 md 的机读块，不要只改这里。
# ---------------------------------------------------------------------------
DEFAULT_BASELINES = {
    "version": "1",
    "tolerance_pct": 20,
    "forms": {
        "极简型": {"chars": [0, 1200]},
        "标准绘本": {"chars": [1500, 6500]},
        "桥梁书": {"chars": [10000, 42714]},
    },
    "genres": {
        "韵文冒险": {"chars": [1570, 4710], "avg_sentence": [8, 10],
                     "refrain_types_min": 3, "refrain_top_freq": [5, 9],
                     "silent_spreads": [2, 6], "onomatopoeia": [3, 10]},
        "互动喜剧": {"chars": [667, 3791], "avg_sentence": [3.5, 9],
                     "refrain_types_min": None, "refrain_top_freq": None,
                     "silent_spreads": [2, 6], "onomatopoeia": [2, 10]},
        "睡前安抚": {"chars": [667, 2589], "avg_sentence": [5, 12],
                     "refrain_types_min": 3, "refrain_top_freq": [5, 9],
                     "silent_spreads": [0, 4], "onomatopoeia": [0, 4]},
        "情绪教育": {"chars": [2010, 9169], "avg_sentence": [9, 12],
                     "refrain_types_min": None, "refrain_top_freq": None,
                     "silent_spreads": [1, 6], "onomatopoeia": [0, 3]},
        "认知概念": {"chars": [972, 3150], "avg_sentence": [8, 12],
                     "refrain_types_min": 3, "refrain_top_freq": [5, 9],
                     "silent_spreads": [2, 6], "onomatopoeia": [2, 10]},
        "荒诞幽默": {"chars": [2010, 12862], "avg_sentence": [7, 12],
                     "refrain_types_min": None, "refrain_top_freq": None,
                     "silent_spreads": [2, 6], "onomatopoeia": [2, 10]},
        "成长仪式": {"chars": [1536, 3429], "avg_sentence": [9, 11],
                     "refrain_types_min": None, "refrain_top_freq": None,
                     "silent_spreads": [2, 6], "onomatopoeia": [0, 4]},
        "民间故事/经典叙事": {"chars": [3201, 12862], "avg_sentence": [9, 14],
                             "refrain_types_min": None, "refrain_top_freq": None,
                             "silent_spreads": [0, 6], "onomatopoeia": [0, 4]},
        "桥梁书/章节": {"chars": [10000, 42714], "avg_sentence": [6, 13],
                        "refrain_types_min": None, "refrain_top_freq": None,
                        "silent_spreads": None, "onomatopoeia": None},
    },
    "sentence_bands": [
        {"label": "超短", "words_per_sentence": [3.5, 5.5], "age": "2-4"},
        {"label": "短", "words_per_sentence": [6, 8], "age": "4-6"},
        {"label": "中", "words_per_sentence": [8, 12], "age": "5-8"},
        {"label": "长", "words_per_sentence": [12, 15], "age": "6-8"},
        {"label": "超长", "words_per_sentence": [15, 999], "age": "亲子共读"},
    ],
    "climax_lines_max": 2,
    "hook_interval_pages": [3, 5],
    "onomatopoeia_per_page_max": 2,
    "slowwrite_uppercase_max": 2,
    "refrain_strength": {"微弱": [3, 4], "标准": [5, 9], "强结构": [10, 14]},
    "silent_spread_by_genre_note": "常规体裁取 2-6 页，睡前安抚取 0-4 页（不制造静默）",
}

# 拟声词库（来源：craft-baselines.md 的「拟声词总库」四类归并）
ONO_LIB = [
    # 行进 / 触感
    "swishy swashy", "splash splosh", "squelch squerch", "stumble trip",
    "crunch", "tap", "scrunch", "scratch", "thump", "bonk", "plunk", "whiff", "whump",
    # 动物叫声
    "beep", "croak", "baaa", "moo", "oink", "cluck", "peep", "maaa", "neigh",
    "quack", "honk", "click clack moo", "woof", "meow", "hoot", "roar", "hiss", "boing",
    # 巨响 / 惊讶
    "boom", "crash", "pop", "zap", "whoosh", "splash", "glug", "plop", "splat",
    # 情绪 / 机械
    "ahhhh", "yippee", "oh-oh", "waaaa", "hooray", "vroom", "buzz", "ding", "clang",
]
ONO_SET = set(ONO_LIB)
ONO_RE = re.compile(r"\b(" + "|".join(re.escape(o) for o in sorted(ONO_LIB, key=len, reverse=True)) + r")\b",
                    re.IGNORECASE)

# 英文停用词（用于首尾回环的实词比对）
STOPWORDS = set("""
a an the and or but if then than so as at by for from in into of on onto to with
is are was were be been being am do does did done have has had will would can could
shall should may might must not no nor it its it's this that these those there here
he she they them his her their we us our you your i me my mine ours theirs
what which who whom whose when where why how all any both each few more most other
some such only own same too very just also now up down out off over under again
about after before while during between above below once
""".split())

SILENT_MARKERS = ("（本页无文字）", "(本页无文字)", "（无文字）", "(无文字)", "（静默跨页）", "(silent spread)")

# 翻页钩子候选：页末问句 / 悬停省略 / 断词破折号（技法卡 C1.4 四型中可正则检出的三型；
# 物品揭晓型与句中断词细节不在射程——密度结果偏保守，只升 WARN 不升 FAIL）
HOOK_END_RE = re.compile(r"(?:[?？]|\.{3,}|…+|—+|-)\s*$")


# ---------------------------------------------------------------------------
# 基准加载：单一权威 = text-craft/references/craft-baselines.md 的机读基准块
# ---------------------------------------------------------------------------
def default_baselines_path():
    """本脚本位于 skills/craft-benchmark-check/scripts/，基准在 skills/text-craft/references/。"""
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.normpath(os.path.join(here, "..", "..", "text-craft", "references", "craft-baselines.md"))
    return cand if os.path.isfile(cand) else None


def load_baselines(explicit=None):
    """返回 (baselines_dict, source_str)。优先显式路径，其次同包基准文件，最后内置兜底。"""
    candidates = []
    if explicit:
        candidates.append((explicit, "命令行指定"))
    auto = default_baselines_path()
    if auto:
        candidates.append((auto, "text-craft 基准文件"))
    for path, label in candidates:
        data = _extract_json_block(path)
        if data and "genres" in data:
            return data, f"{label}（{os.path.basename(path)}）"
    return DEFAULT_BASELINES, "内置兜底（基准文件不可解析）"


def _extract_json_block(path):
    """从 markdown 中提取最后一个 ```json 代码块并解析。"""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    blocks = re.findall(r"```json\s*\n(.*?)```", text, re.DOTALL)
    if not blocks:
        return None
    try:
        return json.loads(blocks[-1])
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# 脚本解析
# ---------------------------------------------------------------------------
def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def parse_markdown_tables(text):
    """切出 markdown 表格块（每块是行列表）。"""
    tables, cur = [], []
    for ln in text.splitlines():
        if ln.strip().startswith("|") and ln.count("|") >= 2:
            cur.append(ln.strip())
        else:
            if len(cur) >= 2:
                tables.append(cur)
            cur = []
    if len(cur) >= 2:
        tables.append(cur)
    return tables


def split_row(row):
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    return cells


def is_separator(row):
    return bool(re.fullmatch(r"\|?[\s:|-]+\|?", row.strip())) and "-" in row


def find_col(header, *keywords):
    """返回首个表头单元格含任一关键词的列索引。"""
    for i, h in enumerate(header):
        hl = h.lower()
        for k in keywords:
            if k.lower() in hl:
                return i
    return None


def parse_script(text):
    """解析逐页脚本表 + 分页大纲 + 标题 + 头部元信息。"""
    result = {"title": None, "meta": {}, "pages": [], "outline": [], "notes": []}

    m = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    if m:
        result["title"] = re.sub(r"\s*[-—]\s*完整脚本\s*$", "", m.group(1)).strip()

    # 头部元信息： - 目标年龄：4-6 / - 预计页数：19
    for key, field in (("目标年龄", "age"), ("预计页数", "pages"),
                       ("文体", "genre"), ("体裁", "genre"),
                       ("目标蓝思范围", "lexile"), ("总字数", "word_count")):
        mm = re.search(r"[-*]?\s*" + key + r"\s*[：:]\s*([^\n|]+)", text)
        if mm:
            result["meta"][field] = mm.group(1).strip()

    for tbl in parse_markdown_tables(text):
        header = split_row(tbl[0])
        body = [r for r in tbl[1:] if not is_separator(r)]
        body = [split_row(r) for r in body]

        # 逐页脚本表
        c_page = find_col(header, "#", "页码", "page")
        c_text = find_col(header, "Text", "英文")
        if c_text is None:
            c_text = find_col(header, "Text ")
        c_illus = find_col(header, "插图", "Illustration", "插描")
        c_zh = find_col(header, "中文", "译文", "Chinese")
        if c_text is not None and c_text != c_page:
            for cells in body:
                if len(cells) <= c_text:
                    continue
                raw_no = cells[c_page] if c_page is not None and len(cells) > c_page else ""
                num = re.search(r"\d+", raw_no or "")
                result["pages"].append({
                    "no": num.group() if num else raw_no,
                    "text": cells[c_text] if cells[c_text] else "",
                    "illus": cells[c_illus] if c_illus is not None and len(cells) > c_illus else "",
                    "zh": cells[c_zh] if c_zh is not None and len(cells) > c_zh else "",
                })
            continue

        # 分页大纲表
        c_func = find_col(header, "叙事功能", "功能")
        c_range = find_col(header, "页码区间", "页码")
        if c_func is not None and c_range is not None:
            for cells in body:
                if len(cells) > max(c_func, c_range):
                    result["outline"].append({
                        "range": cells[c_range], "function": cells[c_func],
                    })
    return result


# ---------------------------------------------------------------------------
# 指标计算
# ---------------------------------------------------------------------------
def is_silent(t):
    ts = (t or "").strip()
    if not ts:
        return True
    return any(mk in ts for mk in SILENT_MARKERS)


def is_hook_page(t):
    return bool(HOOK_END_RE.search((t or "").strip()))


def hook_density(texts):
    """翻页钩子候选页与最长无钩连续页数。

    候选三型（页末问句/悬停省略/破折号断词）是正则可检子集——物品揭晓型与
    句中断词不在射程，密度只做保守下限。无字页重置计数（静默本身即张力）；
    末页豁免（全书终点无需再设钩，但通往末页的尾段仍计入）。
    """
    hooks, max_gap, run = [], 0, 0
    total = len(texts)
    for i, (no, t) in enumerate(texts):
        if i == total - 1:
            break
        if is_silent(t) or is_hook_page(t):
            if not is_silent(t):
                hooks.append(str(no))
            max_gap = max(max_gap, run)
            run = 0
        else:
            run += 1
    return hooks, max(max_gap, run)


def outline_peak_ranges(outline, climax_no):
    """情绪顶点代理段页号集合：大纲叙事功能含「高潮/转折」的页区间 + 显式高潮页。

    无字页「是否置于情绪顶点」不可正则直判，以大纲叙事功能为代理（零假阴性取向：
    只判定可判定项，跌落段等特殊用途交 LLM-judge）。无任何可用信息时返回 None。
    """
    ranges = []
    if outline:
        for row in outline:
            if re.search(r"高潮|转折", row["function"]):
                ranges.extend(parse_page_numbers(row["range"]))
    if climax_no:
        ranges.extend(str(n) for n in climax_no)
    return ranges or None


def strength_tier(freq, tiers):
    """副歌强度分档名（微弱/标准/强结构），不在任何档内返回 None。"""
    for label, (lo, hi) in tiers.items():
        if lo <= freq <= hi:
            return f"{label} {lo}–{hi}"
    return None


def classify_form(char_count, forms_cfg):
    """按全书字符量归入三种形态之一（极简型/标准绘本/桥梁书）；落在区间间隙返回 None。"""
    for name, cfg in forms_cfg.items():
        lo, hi = cfg["chars"]
        if lo <= char_count <= hi:
            return name
    return None


def words_of(t):
    return re.findall(r"[A-Za-z][A-Za-z'\-]*", t or "")


def split_sentences(text):
    parts = re.split(r"(?<=[.!?])[\"')\]]*\s+", (text or "").strip())
    return [p for p in parts if words_of(p)]


def norm_line(line):
    s = re.sub(r"[^A-Za-z' \-]", " ", (line or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def content_words(t):
    return [w for w in (w.lower() for w in words_of(t)) if w not in STOPWORDS and len(w) > 1]


# 全大写但显然不是拟声词的常见词（避免把强调词误计为拟声）
UPPER_STOPWORDS = {"THE", "AND", "YOU", "NOT", "FOR", "BUT", "ALL", "ONE", "TWO", "YES", "OK"}


def count_onomatopoeia(text):
    """拟声词命中数 + 连写三次检测。

    三类合计（零假阴性取向，宁可多报）：
    ① 拟声词库匹配；② 同一词连写三次（`Stomp, stomp, stomp` 式）；③ 独立全大写词（`WHOOSH!` 式）。
    已由词库命中的大写词不重复计入第三类。
    """
    hits, triples, upper = ono_parts(text)
    return len(hits) + len(triples) + len(upper), triples


def ono_parts(text):
    """拟声词三类检出（词库命中 / 连写三次 / 独立全大写），返回 (hits, triples, upper)。"""
    text = text or ""
    hits = ONO_RE.findall(text)
    hit_set = {h.lower() for h in hits}
    triples = re.findall(r"\b(\w+)(?:[,\s]+\1){2}\b", text, re.IGNORECASE)
    triple_set = {t.lower() for t in triples}
    upper = [w for w in re.findall(r"\b[A-Z]{3,}\b", text)
             if w.lower() not in hit_set and w.lower() not in triple_set
             and w not in UPPER_STOPWORDS]
    return hits, triples, upper


def distinct_ono(text):
    """页内不同拟声词种数——「单页 ≤2 个」按声音个数计，标准连写三次算 1 个。"""
    hits, triples, upper = ono_parts(text)
    return ({h.lower() for h in hits} | {t.lower() for t in triples}
            | {w.lower() for w in upper})


def count_slowwrite_uppercase(text):
    """慢写（s-l-o-w-l-y）+ 整行大写爆发页数。"""
    slow = re.findall(r"\b\w+(?:-\w+){2,}\b", text or "")
    upper_lines = 0
    for ln in (text or "").splitlines():
        ws = words_of(ln)
        if len(ws) >= 2 and all(w.isupper() for w in ws) and sum(len(w) for w in ws) >= 6:
            upper_lines += 1
    return len(slow), upper_lines


def parse_page_numbers(spec):
    out = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*[-–~]\s*(\d+)$", part)
        if m:
            out.extend(str(i) for i in range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            out.append(part)
    return out


def compute_metrics(parsed, climax_pages=None, title_word=None):
    pages = parsed["pages"]
    texts = [(p["no"], p["text"]) for p in pages]
    body_text = "\n".join(t for _, t in texts if not is_silent(t))

    silent = [no for no, t in texts if is_silent(t)]
    word_counts = [len(words_of(t)) for _, t in texts if not is_silent(t)]
    total_words = sum(word_counts)
    char_count = len(re.sub(r"\s", "", body_text))

    sents = split_sentences(body_text)
    avg_sentence = (total_words / len(sents)) if sents else 0.0

    # 副歌：按行规范化后统计重复（≥3 次且 ≥2 词）
    line_counter = Counter()
    for _, t in texts:
        for ln in re.split(r"[/\n]|\s{3,}", t or ""):
            n = norm_line(ln)
            if len(words_of(n)) >= 2:
                line_counter[n] += 1
    refrain_types = sum(1 for _, c in line_counter.items() if c >= 3)
    top_line, top_freq = (line_counter.most_common(1)[0] if line_counter else ("", 0))

    ono_total, ono_triples = count_onomatopoeia(body_text)
    slow_n, upper_n = count_slowwrite_uppercase(body_text)

    # 高潮页行数
    climax_no = list(climax_pages or [])
    if not climax_no and parsed["outline"]:
        for row in parsed["outline"]:
            if re.search(r"高潮|climax", row["function"], re.IGNORECASE):
                climax_no = parse_page_numbers(row["range"])
                break
    climax_lines = None
    climax_reported = None
    if climax_no:
        lines = []
        for p in pages:
            if p["no"] in climax_no and not is_silent(p["text"]):
                lines.extend([l for l in re.split(r"[/\n]", p["text"]) if norm_line(l)])
        if lines:
            climax_lines = len(lines)
            climax_reported = ",".join(climax_no)

    # 开篇书名复现
    title_word = title_word or parsed["title"]
    title_hit = None
    if title_word:
        first_body = next((t for _, t in texts if not is_silent(t)), "")
        key = [w for w in words_of(title_word) if w.lower() not in STOPWORDS and len(w) > 2]
        title_hit = bool(key) and any(k.lower() in first_body.lower() for k in key)

    # 首尾回环：首末页实词重合
    first = next((t for _, t in texts if not is_silent(t)), "")
    last = next((t for _, t in reversed(texts) if not is_silent(t)), "")
    fw, lw = set(content_words(first)), set(content_words(last))
    overlap = sorted(fw & lw)
    ring_ratio = (len(overlap) / len(fw)) if fw else 0.0

    # 翻页钩子密度（末页豁免；无字页重置计数）
    hook_pages, hook_max_gap = hook_density(texts)

    # 单页拟声词用量（不同拟声词种数；仅记录命中页）
    ono_per_page = []
    for no, t in texts:
        if is_silent(t):
            continue
        n = len(distinct_ono(t))
        if n:
            ono_per_page.append((no, n))

    # 副歌末段打破：最高频行在末四分之一原样出现即"未打破"信号；
    # 成环收尾（末个有字页复现开篇副歌，技法卡 C7.3）是技法而非未打破，豁免
    refrain_late = []
    if top_freq >= 3 and top_line:
        total = len(texts)
        start = total - total // 4
        first_lines = {norm_line(l) for l in re.split(r"[/\n]", first) if norm_line(l)}
        last_no = next((no for no, t in reversed(texts) if not is_silent(t)), None)
        for no, t in texts[start:]:
            if is_silent(t) or not no:
                continue
            page_lines = {norm_line(l) for l in re.split(r"[/\n]", t)}
            if top_line in page_lines and not (no == last_no and top_line in first_lines):
                refrain_late.append(str(no))

    # 无字页位置：情绪顶点代理 = 分页大纲高潮/转折段 ∪ 显式高潮页；无任何信息时不可判
    peaks = outline_peak_ranges(parsed.get("outline"), climax_no)
    silent_misplaced = None
    if peaks is not None:
        peak_set = set(peaks)
        silent_misplaced = [str(no) for no, t in texts
                            if is_silent(t) and no and str(no) not in peak_set]

    return {
        "page_total": len(pages),
        "silent_pages": silent,
        "silent_count": len(silent),
        "word_total": total_words,
        "char_total": char_count,
        "sentence_count": len(sents),
        "avg_sentence": round(avg_sentence, 2),
        "refrain_types": refrain_types,
        "refrain_top_line": top_line,
        "refrain_top_freq": top_freq,
        "onomatopoeia_total": ono_total,
        "onomatopoeia_triples": ono_triples,
        "slowwrite_uppercase": slow_n + upper_n,
        "climax_pages": climax_reported,
        "climax_lines": climax_lines,
        "title_repeats_opening": title_hit,
        "ring_overlap_words": overlap,
        "ring_ratio": round(ring_ratio, 3),
        "hook_pages": hook_pages,
        "hook_max_gap": hook_max_gap,
        "ono_per_page": ono_per_page,
        "refrain_late_verbatim": refrain_late,
        "silent_misplaced": silent_misplaced,
        "first_text": first[:120],
        "last_text": last[:120],
    }


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
def band_verdict(value, lo, hi, tol_pct):
    """区间判定：内 PASS / 越界 WARN / 越界超容忍度 FAIL。"""
    if value is None:
        return "N/A", ""
    if lo <= value <= hi:
        return "PASS", ""
    span = max(abs(hi - lo), 1)
    over = (lo - value) if value < lo else (value - hi)
    if over / span * 100 > tol_pct:
        return "FAIL", f"越界超过容忍度 {tol_pct}%（区间 {lo}–{hi}，实际 {value}）"
    return "WARN", f"越界但在容忍度内（区间 {lo}–{hi}，实际 {value}）"


def norm_age(a):
    """把 4-6 / 4-6岁 / 4 - 6 归一为 4-6。"""
    nums = re.findall(r"\d+", str(a or ""))
    return "-".join(nums[:2]) if nums else ""


def evaluate(metrics, baselines, genre, age=None):
    tol = baselines.get("tolerance_pct", TOLERANCE_PCT)
    g = (baselines.get("genres") or {}).get(genre) if genre else None
    rows = []

    def add(name, value, verdict, note):
        rows.append({"metric": name, "value": value, "verdict": verdict, "note": note})

    if not g:
        # 无体裁基准：形态分档兜底（极简型/标准绘本/桥梁书），跨体裁通用项照常输出
        form = classify_form(metrics["char_total"], baselines.get("forms") or {})
        if form:
            add("形态分档", form, "INFO",
                f"按字符量 {metrics['char_total']} 归入{form}；可用 --genre 指定体裁做分体裁对标")
        else:
            add("形态分档", "区间间隙", "INFO",
                f"字符量 {metrics['char_total']} 未落入任一形态区间；可用 --genre 指定体裁做分体裁对标")

    if g:
        v, n = band_verdict(metrics["char_total"], g["chars"][0], g["chars"][1], tol)
        add("全书字符量（去空白）", metrics["char_total"], v, n or f"基准 {g['chars'][0]}–{g['chars'][1]}")

        v, n = band_verdict(metrics["avg_sentence"], g["avg_sentence"][0], g["avg_sentence"][1], tol)
        add("平均句长（词/句）", metrics["avg_sentence"], v, n or f"基准 {g['avg_sentence'][0]}–{g['avg_sentence'][1]}")

        if g.get("refrain_types_min"):
            ok = metrics["refrain_types"] >= g["refrain_types_min"]
            add("副歌句式种数", metrics["refrain_types"], "PASS" if ok else "FAIL",
                f"体裁要求 ≥{g['refrain_types_min']} 种")
        else:
            add("副歌句式种数", metrics["refrain_types"], "INFO", "该体裁不设副歌种数要求")

        if g.get("refrain_top_freq"):
            lo, hi = g["refrain_top_freq"]
            v, n = band_verdict(metrics["refrain_top_freq"], lo, hi, tol)
            tier = strength_tier(metrics["refrain_top_freq"], baselines.get("refrain_strength") or {})
            note = n or f"标准强度 {lo}–{hi}"
            if tier:
                note = f"{note}（{tier}）"
            add("最高频重复行次数", metrics["refrain_top_freq"], v, note)

        if g.get("silent_spreads"):
            lo, hi = g["silent_spreads"]
            v, n = band_verdict(metrics["silent_count"], lo, hi, tol)
            add("无字页数量", metrics["silent_count"], v, n or f"基准 {lo}–{hi}")

        if g.get("onomatopoeia"):
            lo, hi = g["onomatopoeia"]
            v, n = band_verdict(metrics["onomatopoeia_total"], lo, hi, tol)
            add("拟声词命中总数", metrics["onomatopoeia_total"], v, n or f"基准 {lo}–{hi}")

    # 目标年龄档对标（句长第一杠杆；不依赖体裁，无体裁时仍输出）
    bands = baselines.get("sentence_bands") or []
    band = next((b for b in bands if norm_age(b.get("age")) == norm_age(age)), None) if age else None
    if band:
        lo, hi = band["words_per_sentence"]
        v, n = band_verdict(metrics["avg_sentence"], lo, hi, tol)
        add("句长对目标年龄档", metrics["avg_sentence"], v,
            n or f"年龄 {norm_age(age)} 对应{band['label']}档 {lo}–{hi}")
    elif age:
        add("句长对目标年龄档", metrics["avg_sentence"], "N/A",
            f"年龄 {age} 未匹配基准分档，跳过对标")

    # ---- 跨体裁通用项（无论有无体裁均输出）----

    # 高潮页行数
    cmax = baselines.get("climax_lines_max", 2)
    if metrics["climax_lines"] is not None:
        v = "PASS" if metrics["climax_lines"] <= cmax else "FAIL"
        add("高潮页文字行数", metrics["climax_lines"], v,
            f"上限 {cmax} 行（页 {metrics['climax_pages']}）")
    else:
        add("高潮页文字行数", "未识别", "N/A",
            "未能从分页大纲识别高潮页，可用 --climax-pages 指定")

    # 翻页钩子密度：候选为正则可检三型，密度只是保守下限——只升 WARN 不升 FAIL
    hlo, hhi = (baselines.get("hook_interval_pages") or [3, 5])
    hooks_note = (f"间隔基准 {hlo}–{hhi} 页；候选页 {('、'.join(metrics['hook_pages']) or '无')}；"
                  f"最长无钩连续 {metrics['hook_max_gap']} 页（候选含页末问句/悬停省略/破折号，"
                  f"物品揭晓型与句中断词不在射程，密度偏保守）")
    if metrics["hook_max_gap"] <= hhi:
        add("翻页钩子密度", f"最长 {metrics['hook_max_gap']} 页", "PASS", hooks_note)
    else:
        add("翻页钩子密度", f"最长 {metrics['hook_max_gap']} 页", "WARN", hooks_note)

    # 单页拟声词用量：不同拟声词种数计，标准连写三次算 1 个
    omax = baselines.get("onomatopoeia_per_page_max", 2)
    over = [(no, n) for no, n in metrics["ono_per_page"] if n > omax]
    if over:
        pages_str = "、".join(f"页 {no}（{n} 个）" for no, n in over)
        add("单页拟声词用量", pages_str, "WARN",
            f"超单页上限 {omax} 个（按不同拟声词种数）")
    else:
        add("单页拟声词用量", f"最重 {max((n for _, n in metrics['ono_per_page']), default=0)} 个", "PASS",
            f"单页上限 {omax} 个（按不同拟声词种数）")

    # 副歌末段打破：正则可检信号，成环收尾豁免；语义判定交 LLM-judge
    if metrics["refrain_late_verbatim"]:
        late_str = "、".join(m for m in metrics["refrain_late_verbatim"])
        add("副歌末段打破", f"页 {late_str} 原样复现", "WARN",
            f"最高频行「{metrics['refrain_top_line'][:30]}…」在末四分之一原样出现，未见打破信号；成环收尾已豁免")
    elif metrics["refrain_top_freq"] >= 3:
        add("副歌末段打破", "末段未见原样复现", "PASS",
            "最高频重复行在末四分之一未原样出现（变奏或收束）")
    else:
        add("副歌末段打破", "无副歌结构", "INFO",
            "全书无 ≥3 次重复行，不适用副歌打破检查")

    # 无字页位置：大纲高潮/转折段为情绪顶点代理
    if metrics["silent_misplaced"] is None:
        add("无字页位置", "不可判", "INFO",
            "无分页大纲且未指定高潮页，无法以叙事功能判定静默页是否在情绪顶点")
    elif metrics["silent_misplaced"]:
        add("无字页位置", f"页 {'、'.join(metrics['silent_misplaced'])} 偏离顶点", "WARN",
            "无字页未落在分页大纲的高潮/转折段（情绪顶点代理）；特殊用途（跌落段等）交 LLM-judge 复核")
    else:
        add("无字页位置", f"{metrics['silent_count']} 页均在顶点段", "PASS",
            "全部无字页落在分页大纲的高潮/转折段（情绪顶点代理）")

    smax = baselines.get("slowwrite_uppercase_max", 2)
    v = "PASS" if metrics["slowwrite_uppercase"] <= smax else "WARN"
    add("慢写/大写排版处数", metrics["slowwrite_uppercase"], v, f"上限 {smax} 处")

    if metrics["title_repeats_opening"] is None:
        add("开篇复现书名", "无法判定", "N/A", "未解析到标题，可用 --title-word 指定关键词")
    else:
        add("开篇复现书名", "是" if metrics["title_repeats_opening"] else "否", "INFO",
            "韵文类应为是；纯叙事类可免")

    rv = "INFO"
    rn = f"首末页实词重合 {len(metrics['ring_overlap_words'])} 个（占比 {metrics['ring_ratio']}）"
    add("首尾回环度", metrics["ring_ratio"], rv,
        rn + "；循环类结构应强回环，线性叙事可不回环")
    return rows, tol, g


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def render(path, parsed, metrics, rows, genre, baselines_source, baselines, age=None):
    L = []
    L.append(f"# 文字工艺量化自测报告")
    L.append("")
    L.append(f"- 脚本：`{path}`")
    L.append(f"- 标题：{parsed['title'] or '（未解析）'}")
    L.append(f"- 体裁：{genre or '（未指定，仅记录指标）'}")
    L.append(f"- 目标年龄：{age or '（未指定）'}")
    L.append(f"- 页数：{metrics['page_total']}")
    if parsed["meta"]:
        meta = " / ".join(f"{k}={v}" for k, v in parsed["meta"].items())
        L.append(f"- 脚本头部元信息：{meta}")
    L.append(f"- 阈值来源：{baselines_source}（容忍度 {baselines.get('tolerance_pct', TOLERANCE_PCT)}%）")
    L.append("")
    L.append("## 指标对标")
    L.append("")
    L.append("| # | 指标 | 实际值 | 判定 | 说明 |")
    L.append("|---|------|--------|------|------|")
    for i, r in enumerate(rows, 1):
        L.append(f"| {i:02d} | {r['metric']} | {r['value']} | {r['verdict']} | {r['note']} |")
    L.append("")
    L.append("## 明细")
    L.append("")
    L.append(f"- 全书词量：{metrics['word_total']}　字符量（去空白）：{metrics['char_total']}　句数：{metrics['sentence_count']}")
    L.append(f"- 无字页：{metrics['silent_count']} 页 —— {('、'.join(metrics['silent_pages']) if metrics['silent_pages'] else '无')}")
    L.append(f"- 副歌最高频行：「{metrics['refrain_top_line']}」（{metrics['refrain_top_freq']} 次），重复句式 {metrics['refrain_types']} 种")
    L.append(f"- 拟声词：命中 {metrics['onomatopoeia_total']} 处；连写三次 {len(metrics['onomatopoeia_triples'])} 组")
    if metrics["ono_per_page"]:
        per_page = "；".join(f"页 {no}={n} 个" for no, n in metrics["ono_per_page"])
        L.append(f"- 单页拟声词分布：{per_page}")
    if metrics["hook_pages"]:
        L.append(f"- 翻页钩子候选页：{('、'.join(metrics['hook_pages']))}（最长无钩连续 {metrics['hook_max_gap']} 页）")
    else:
        L.append(f"- 翻页钩子候选页：无（最长无钩连续 {metrics['hook_max_gap']} 页）")
    if metrics["refrain_late_verbatim"]:
        L.append(f"- 副歌末段原样复现页：{('、'.join(metrics['refrain_late_verbatim']))}")
    if metrics["silent_misplaced"]:
        L.append(f"- 无字页偏离顶点：页 {('、'.join(metrics['silent_misplaced']))}")
    L.append(f"- 首尾回环：{('、'.join(metrics['ring_overlap_words']) if metrics['ring_overlap_words'] else '无实词重合')}")
    L.append(f"- 开篇首句：{metrics['first_text']}")
    L.append(f"- 收尾末句：{metrics['last_text']}")

    fails = [r for r in rows if r["verdict"] == "FAIL"]
    warns = [r for r in rows if r["verdict"] == "WARN"]
    L.append("")
    L.append("## 结论")
    L.append("")
    L.append(f"- FAIL {len(fails)} 项　WARN {len(warns)} 项")
    if fails:
        L.append("- 须回炉修正：")
        for r in fails:
            L.append(f"  - {r['metric']}：{r['note']}")
    else:
        L.append("- 无越界超容忍度项。")
    L.append("")
    L.append("> 本报告为**确定型指标检查**，只覆盖可计数的工艺维度。"
             "语义维度（朗读体验、图文互补、情绪具象）不在此列，仍由 LLM-judge 与人工朗读测试承担。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="绘本文字工艺量化自测")
    ap.add_argument("script", help="逐页脚本文件（markdown，四列格式）")
    ap.add_argument("--genre", default=None, help="体裁（匹配 craft-baselines 的分体裁基准）")
    ap.add_argument("--age", default=None, help="目标年龄（仅记录，用于句长档提示）")
    ap.add_argument("--baselines", default=None, help="基准文件路径（默认自动定位 text-craft 基准）")
    ap.add_argument("--climax-pages", default=None, help="高潮页号，逗号分隔，如 11,12")
    ap.add_argument("--title-word", default=None, help="书名关键词（用于开篇复现检查）")
    ap.add_argument("--json", dest="json_out", default=None, help="同时输出 JSON 结果到该路径")
    args = ap.parse_args()

    if not os.path.isfile(args.script):
        print(f"[craft-benchmark] 文件不存在：{args.script}", file=sys.stderr)
        return 2

    text = read_text(args.script)
    parsed = parse_script(text)
    if not parsed["pages"]:
        print("[craft-benchmark] 未能解析出逐页脚本表。请确认脚本采用 markdown 四列表"
              "（首列为页码、含 Text/英文 列）。", file=sys.stderr)
        return 2

    baselines, src = load_baselines(args.baselines)
    genre = args.genre or parsed["meta"].get("genre")
    age = args.age or parsed["meta"].get("age")
    metrics = compute_metrics(parsed, parse_page_numbers(args.climax_pages), args.title_word)
    rows, tol, g = evaluate(metrics, baselines, genre, age)

    print(render(args.script, parsed, metrics, rows, genre, src, baselines, age))

    if args.json_out:
        payload = {
            "script": args.script, "title": parsed["title"], "genre": genre,
            "baselines_source": src, "metrics": metrics, "findings": rows,
        }
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n[json] 已写入 {args.json_out}")

    return 1 if any(r["verdict"] == "FAIL" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
