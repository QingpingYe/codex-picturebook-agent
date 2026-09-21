"""Read-only CLI for the Feishu authority knowledge loader."""

import argparse
from pathlib import Path
import getpass
import json
import re
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
STORE_SCRIPTS = SCRIPT_DIR.parent.parent / "feishu-knowledge-store" / "scripts"
for scripts_path in (STORE_SCRIPTS, SCRIPT_DIR):
    if str(scripts_path) not in sys.path:
        sys.path.insert(0, str(scripts_path))

from authority import AuthorityLoader, AuthorityQuery
from cache import KnowledgeCache
from config import load_config
from config_paths import resolve_config_path
from lark_cli_bootstrap import ensure_lark_cli
from load_knowledge import (
    KnowledgeEvidenceBundle,
    bundle_from_dict,
    bundle_to_dict,
)
from store_cli import build_components


def safe_user(getuser=None) -> str:
    try:
        raw = (getuser or getpass.getuser)()
    except Exception:
        return "local-user"
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(raw)).strip("._")
    return value or "local-user"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read Feishu authority knowledge")
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser("load")
    load.add_argument("--project-id", required=True)
    load.add_argument("--series-id", required=True)
    load.add_argument("--page-types", required=True)
    load.add_argument("--workspace")
    load.add_argument("--config")
    load.add_argument("--allow-offline-cache", action="store_true")
    return parser


def _error_payload(error: Exception) -> dict:
    if hasattr(error, "to_dict"):
        return error.to_dict()
    return {"status": "runtime_error", "error": str(error)}


def main(argv=None, stdout=None, components_factory=None, cli_probe=None,
         getuser=None, environ=None) -> int:
    stdout = stdout if stdout is not None else sys.stdout
    args = build_parser().parse_args(argv)
    components_factory = components_factory or build_components
    cli_probe = cli_probe or ensure_lark_cli
    workspace = Path(args.workspace) if args.workspace else Path.cwd()
    try:
        resolved = resolve_config_path(args.config, workspace, environ)
        config = load_config(resolved.path, resolved.path.parent, environ)
        cache_store = KnowledgeCache(workspace, safe_user(getuser))
        cached_bundle = bundle_from_dict(cache_store.load())
        page_types = tuple(
            value.strip() for value in args.page_types.split(",") if value.strip()
        )
        query = AuthorityQuery(args.project_id, args.series_id, page_types)
        try:
            cli_status = cli_probe(candidates=config.cli_candidates, environ=environ)
            if cli_status.get("status") != "available":
                if not (args.allow_offline_cache and cached_bundle is not None):
                    raise RuntimeError("lark-cli is unavailable")
            components = components_factory(
                resolved.path, environ=environ, workspace=workspace,
            )
            loader = AuthorityLoader(
                components.control_plane, components.cli,
                cache_store=cache_store, cached_bundle=cached_bundle,
            )
            bundle = loader.load(query, allow_offline_cache=args.allow_offline_cache)
        except Exception as error:
            if not (args.allow_offline_cache and cached_bundle is not None):
                raise
            bundle = KnowledgeEvidenceBundle(
                items=cached_bundle.items,
                warnings=(
                    "目标飞书知识库不可用，正在使用最后确认的本地缓存；"
                    "它是非权威版本。",
                    *cached_bundle.warnings,
                ),
                offline=True,
                fetched_at=cached_bundle.fetched_at,
            )
        payload = bundle_to_dict(bundle)
        payload["cache_path"] = str(cache_store.path)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=stdout)
        return 0
    except Exception as error:
        print(json.dumps(_error_payload(error), ensure_ascii=False, sort_keys=True), file=stdout)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
