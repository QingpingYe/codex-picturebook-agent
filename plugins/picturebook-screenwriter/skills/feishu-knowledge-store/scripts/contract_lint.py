"""Offline contract checker for read-only Feishu knowledge-store fixtures."""

import argparse
import json
from pathlib import Path
from typing import Any

from control_plane import ControlPlaneCorrupt, _index_entry, _parse_control
from remote_markdown import normalize_remote_markdown
from page_codec import PageCodecError, parse_remote_page


def run_lint(fixture: str | Path) -> dict[str, Any]:
    fixture = Path(fixture)
    errors: list[str] = []
    warnings: list[str] = []

    try:
        tree = json.loads((fixture / "tree.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"cannot read tree fixture: {error}")
        tree = []
    if not isinstance(tree, list):
        errors.append("tree fixture must be a JSON array")
        tree = []

    try:
        index_content = normalize_remote_markdown(
            (fixture / "index.md").read_text(encoding="utf-8"),
            kind="control",
        )
        payload = _parse_control(index_content, "# AI_KB_INDEX_V1")
        if set(payload) != {"schema_version", "entries"} or payload.get("schema_version") != 1:
            raise ControlPlaneCorrupt("invalid index schema")
        if not isinstance(payload.get("entries"), list):
            raise ControlPlaneCorrupt("invalid index entries")
        entries = {}
        for raw in payload["entries"]:
            try:
                entry = _index_entry(raw)
            except (ControlPlaneCorrupt, TypeError, ValueError) as error:
                errors.append(f"invalid index record: {error}")
                continue
            if entry.key in entries:
                errors.append(f"duplicate logical key: {entry.key}")
                continue
            entries[entry.key] = entry
    except (OSError, ControlPlaneCorrupt, TypeError, ValueError) as error:
        errors.append(f"invalid index fixture: {error}")
        entries = {}

    pages_by_key: dict[str, Any] = {}
    for page_path in sorted((fixture / "pages").glob("*.md")):
        try:
            page = parse_remote_page(page_path.read_text(encoding="utf-8"))
        except (OSError, PageCodecError) as error:
            errors.append(f"invalid page fixture {page_path.name}: {error}")
            continue
        key = page.metadata["key"]
        expected_name = f"{key.replace('/', '__')}.md"
        if page_path.name != expected_name:
            errors.append(f"page filename does not match logical key: {page_path.name}")
        if key in pages_by_key:
            errors.append(f"duplicate logical key: {key}")
        pages_by_key[key] = page

    for key, entry in entries.items():
        page = pages_by_key.get(key)
        if page is None:
            errors.append(f"missing page for index key: {key}")
            continue
        metadata = page.metadata
        if metadata["key"] != entry.key:
            errors.append(f"page key does not match index: {key}")
        if metadata["page_type"] != key.split("/")[-1]:
            errors.append(f"page_type does not match logical key: {key}")
        if metadata["source_revisions"] != entry.source_revisions:
            errors.append(f"page source revisions do not match index: {key}")
        if metadata["last_ai_revision_id"] != entry.last_ai_revision_id:
            errors.append(f"page revision does not match index: {key}")
        if entry.last_seen_revision_id < entry.last_ai_revision_id:
            errors.append(
                f"index last_seen_revision_id precedes last_ai_revision_id: {key}"
            )
        matches = [
            node for node in tree
            if isinstance(node, dict) and node.get("node_token") == entry.wiki_node_token
        ]
        if len(matches) != 1:
            errors.append(
                f"index node token is represented by {len(matches)} tree nodes: {key}"
            )

    for key in sorted(set(pages_by_key) - set(entries)):
        errors.append(f"page has no index entry: {key}")

    return {
        "errors": errors,
        "warnings": warnings,
        "exit_code": 1 if errors else 0,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check offline knowledge-store fixtures")
    parser.add_argument("--fixture", required=True)
    args = parser.parse_args(argv)
    result = run_lint(args.fixture)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
