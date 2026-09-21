#!/usr/bin/env python3
"""wiki-ingest v9 — Staging Validation & Manifest Generator

从 LLM（SKILL.md）合成的 wiki_staging/ 结构化文件读取内容，执行校验，
生成层级化 _manifest.json。不再包含硬编码条目——内容合成全部由 LLM 驱动。

用法：
    python generate_entries.py [--validate-only] [--staging-dir wiki_staging]

输出：
    - 校验报告（PASS/FAIL/WARN）
    - wiki_staging/_manifest.json（层级化格式）
"""

import json
import os
import sys
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from glob import glob
from collections import defaultdict

STORE_SCRIPTS = Path(__file__).resolve().parents[2] / "feishu-knowledge-store" / "scripts"
if str(STORE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(STORE_SCRIPTS))

from shared_schema import (
    COMMON_TYPES,
    DUAL_SCOPE_TYPES,
    PROJECT_TYPES,
    SYSTEM_TYPES,
    logical_key,
    normalize_project_id,
    normalize_series_id,
)

# --- Config ---
STAGING_DIR = "wiki_staging"
CST = timezone(timedelta(hours=8))

# Required frontmatter fields per page_type
REQUIRED_FIELDS = [
    "title", "series_id", "page_type", "source_feishu_url",
    "revision_id", "extracted_at", "source_node_tokens",
    "source_revision_parts",
]

VALID_PAGE_TYPES = [
    "ip-overview", "creation-standards", "quality-rubric", "market-research",
    "worldview", "characters", "content-spec", "corrections",
    "creative-feature-ledger", "golden-sentence-registry", "prop-registry",
    "story-fingerprint-spec", "references",
    "index", "log",
]

def candidate_key(frontmatter: dict[str, object]) -> str:
    """Return the stable logical key used by duplicate and sync layers."""
    page_type = str(frontmatter["page_type"])
    series_id = normalize_series_id(page_type, str(frontmatter.get("series_id", "")))
    project_id = normalize_project_id(page_type, str(frontmatter.get("project_id", "")))
    return logical_key(series_id, project_id, page_type)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def source_revision_vector(frontmatter: dict[str, object]) -> dict[str, str]:
    """Pair source node tokens with their revisions and validate the pairing."""
    tokens = _string_list(frontmatter.get("source_node_tokens"))
    revisions = _string_list(frontmatter.get("source_revision_parts"))

    if not tokens:
        raise ValueError("source_node_tokens must be a non-empty list")
    if len(tokens) != len(revisions):
        raise ValueError("source_node_tokens and source_revision_parts must have equal lengths")
    if len(tokens) != len(set(tokens)):
        raise ValueError("source_node_tokens contains duplicate values")
    if any(not token for token in tokens):
        raise ValueError("source_node_tokens cannot contain blank values")
    if any(not revision for revision in revisions):
        raise ValueError("source_revision_parts cannot contain blank values")

    return dict(zip(tokens, revisions))


def extract_frontmatter(content):
    """Extract YAML frontmatter from a markdown file. Returns (dict, body_start_line)."""
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, 0

    fm_lines = []
    end = 0
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i + 1
            break
        fm_lines.append(lines[i])

    # Simple YAML-like dict parser (key: value pairs and simple lists)
    fm = {}
    current_key = None
    current_list = None

    def item_value(raw):
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        return value

    for line in fm_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # List continuation (indented)
        if line.startswith("  - ") and current_list is not None:
            current_list.append(item_value(line.strip()[2:]))
            continue

        # Key: value
        match = re.match(r'^(\w[\w_]*)\s*:\s*(.*)', stripped)
        if match:
            key = match.group(1)
            value = match.group(2).strip()

            # Handle null/empty
            if value in ("", '""', "null", "~"):
                fm[key] = "" if key in ("project_id", "series_id") else None
            elif value == "true":
                fm[key] = True
            elif value == "false":
                fm[key] = False
            elif value.startswith('"') and value.endswith('"'):
                fm[key] = value[1:-1]
            else:
                fm[key] = value

            current_key = key
            current_list = None
        elif stripped.startswith("- ") and current_key:
            # Start of a list
            current_list = [item_value(stripped[2:])]
            fm[current_key] = current_list

    return fm, end


def parse_page_type(fm):
    """Get page_type from frontmatter."""
    return fm.get("page_type", "")


def validate_file(filepath):
    """Validate a single staging file. Returns list of (level, message) tuples."""
    issues = []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return [("FAIL", f"Cannot read file: {e}")]

    if not content.strip():
        return [("FAIL", "File is empty")]

    fm, body_start = extract_frontmatter(content)

    # Check required base fields
    for field in REQUIRED_FIELDS:
        val = fm.get(field)
        if val is None or val == "":
            # series_id can be empty for index/log
            if field == "series_id":
                continue
            issues.append(("FAIL", f"Missing required field: '{field}'"))

    # Check page_type is valid
    pt = parse_page_type(fm)
    if pt and pt not in VALID_PAGE_TYPES:
        issues.append(("FAIL", f"Invalid page_type: '{pt}'"))

    try:
        source_revision_vector(fm)
    except ValueError as exc:
        issues.append(("FAIL", f"Invalid source revision vector: {exc}"))

    if pt in PROJECT_TYPES or pt in DUAL_SCOPE_TYPES:
        pid = fm.get("project_id", "")
        if not pid:
            allowed = "项目标识" if pt in PROJECT_TYPES else "common 或项目标识"
            issues.append(
                ("FAIL",
                 f"页型 '{pt}' 必须显式声明 project_id（{allowed}）")
            )
    elif pt in SYSTEM_TYPES:
        if fm.get("project_id"):
            issues.append(("WARN", f"System page '{pt}' should have empty project_id"))

    # Check body content
    body = content[body_start:] if body_start else content
    if len(body.strip()) < 50:
        issues.append(("WARN",
                       f"Body content is very short ({len(body.strip())} chars)"))

    # Check machine-data blocks for ledger files
    if pt in ("creative-feature-ledger", "prop-registry", "story-fingerprint-spec"):
        if "<!-- machine-data:" not in body:
            issues.append(("WARN", "Ledger file missing machine-data block"))

    # Check source citation for non-system files
    if pt not in ("index", "log"):
        has_citation = "> 来源：" in body or "> 原文引用" in body
        if not has_citation:
            issues.append(("WARN", "File may be missing source citations"))

    return issues


def discover_files(staging_dir):
    """Discover all .md files recursively in staging dir, excluding meta files."""
    pattern = os.path.join(staging_dir, "**", "*.md")
    files = [f for f in glob(pattern, recursive=True)
             if not os.path.basename(f).startswith("_")]
    return sorted(files)


def parse_series_project(rel_path):
    """Parse series_id and project_id from relative path.
    Returns (series_id, project_id, page_name) or (None, None, None) for root files.
    """
    parts = rel_path.replace("\\", "/").split("/")

    # Root files (index.md, log.md)
    if len(parts) == 1:
        name = parts[0].replace(".md", "")
        return ("system", "system", name)

    # common/ files
    if len(parts) == 2 and parts[0] == "common":
        name = parts[1].replace(".md", "")
        return ("海外绘本", "common", name)

    # Project files
    if len(parts) == 2:
        name = parts[1].replace(".md", "")
        return ("海外绘本", parts[0], name)

    return (None, None, None)


def build_manifest(staging_dir, file_issues):
    """Build hierarchical manifest from staging files."""
    manifest = {
        "version": 9,
        "generated_at": datetime.now(CST).isoformat(),
        "series": {},
        "root": [],
        "entries": [],
    }

    series_data = defaultdict(lambda: {"common": [], "projects": defaultdict(list)})

    for fpath, issues in file_issues.items():
        rel_path = os.path.relpath(fpath, staging_dir).replace("\\", "/")

        try:
            with open(fpath, "r", encoding="utf-8") as f:
                fm, _ = extract_frontmatter(f.read())
            key = candidate_key(fm)
            source_revisions = source_revision_vector(fm)
        except (OSError, KeyError, ValueError) as exc:
            issues.append(("FAIL", f"Cannot build manifest entry: {exc}"))
            continue

        series_id = str(fm.get("series_id", ""))
        project_id = str(fm.get("project_id", ""))
        page_name = str(fm.get("page_type", ""))

        entry = {
            "path": rel_path,
            "key": key,
            "source_revisions": source_revisions,
            "page_type": page_name,
            "series_id": series_id,
            "project_id": project_id,
            "has_errors": any(level == "FAIL" for level, _ in issues),
            "has_warnings": any(level == "WARN" for level, _ in issues),
            "issue_count": len(issues),
        }

        if not series_id:
            manifest["root"].append(entry)
        elif not project_id:
            series_data[series_id]["common"].append(entry)
        else:
            series_data[series_id]["projects"][project_id].append(entry)

        manifest["entries"].append(entry)

    manifest["entries"].sort(key=lambda entry: (entry["key"], entry["path"]))

    # Convert defaultdict to plain dict
    for sid in series_data:
        manifest["series"][sid] = {
            "common": series_data[sid]["common"],
            "projects": dict(series_data[sid]["projects"]),
        }

    return manifest


def check_cross_file_dups(files):
    """跨文件逻辑键查重：candidate_key 相同 → 每个涉及文件各挂一条 FAIL。

    同步硬闸的前置防线：staging 内两个文件指向同一逻辑条目时，任何写入都是错的。
    """
    groups = {}
    invalid = []
    for fpath in files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            fm, _end = extract_frontmatter(content)
            key = candidate_key(fm)
        except (OSError, KeyError, ValueError) as exc:
            invalid.append((fpath, exc))
            continue
        groups.setdefault(key, []).append(fpath)

    issues = []
    if files:
        base = os.path.commonpath([os.path.dirname(f) for f in files]) \
            if len(files) > 1 else os.path.dirname(files[0])
    else:
        base = ""
    for fpath, exc in invalid:
        issues.append(("FAIL",
                       f"无法推导逻辑键（{exc}）: {os.path.relpath(fpath, base)}"))
    for key, paths in groups.items():
        if len(paths) > 1:
            rels = [os.path.relpath(p, base) for p in paths]
            for p in paths:
                issues.append(("FAIL",
                               f"logical key 重复（「{key}」）: {', '.join(rels)}"))
    return issues


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="wiki-ingest v9 staging validator")
    parser.add_argument("--validate-only", action="store_true",
                        help="Only validate, don't generate manifest")
    parser.add_argument("--staging-dir", default=STAGING_DIR,
                        help=f"Staging directory (default: {STAGING_DIR})")
    args = parser.parse_args(argv)

    staging_dir = args.staging_dir
    if not os.path.isdir(staging_dir):
        print(f"ERROR: Staging directory '{staging_dir}' not found.")
        return 1

    files = discover_files(staging_dir)
    print(f"Found {len(files)} staging files in {staging_dir}/")

    # Validate all files
    file_issues = {}
    total_fails = 0
    total_warns = 0
    total_oks = 0

    for fpath in files:
        rel_path = os.path.relpath(fpath, staging_dir)
        issues = validate_file(fpath)

        file_issues[fpath] = issues
        fails = [i for i in issues if i[0] == "FAIL"]
        warns = [i for i in issues if i[0] == "WARN"]

        if fails:
            total_fails += len(fails)
            status = "FAIL"
        elif warns:
            total_warns += len(warns)
            status = "WARN"
        else:
            total_oks += 1
            status = "OK"

        print(f"  [{status}] {rel_path}")
        for level, msg in issues:
            print(f"    {level}: {msg}")

    # 跨文件规范标题查重（写库硬闸的前置防线）
    dup_issues = check_cross_file_dups(files)
    if dup_issues:
        print("\n--- Cross-file duplicate check ---")
        for level, msg in dup_issues:
            print(f"    {level}: {msg}")
        total_fails += len(dup_issues)

    # Summary
    print("\n--- Validation Summary ---")
    print(f"  OK:   {total_oks}")
    print(f"  WARN: {total_warns}")
    print(f"  FAIL: {total_fails}")

    if total_fails > 0:
        print(f"\nERROR: {total_fails} validation failures. Fix before uploading.")
        if args.validate_only:
            return 1

    if not args.validate_only:
        manifest = build_manifest(staging_dir, file_issues)
        manifest_path = os.path.join(staging_dir, "_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        print(f"\nManifest written to {manifest_path}")

        # Count totals
        total_entries = len(manifest["root"])
        for sid, sdata in manifest["series"].items():
            total_entries += len(sdata["common"])
            for pid, pfiles in sdata["projects"].items():
                total_entries += len(pfiles)
        print(f"Total manifest entries: {total_entries}")

    return 0 if total_fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
