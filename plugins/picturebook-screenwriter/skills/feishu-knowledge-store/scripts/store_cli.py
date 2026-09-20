"""Operator boundary for the Feishu knowledge store."""

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import load_config
from control_plane import ControlPlane
from lark_cli import LarkCli
from publisher import Publisher
from sync_runner import SyncRunner


@dataclass(frozen=True)
class Components:
    config: Any
    cli: Any
    publisher: Publisher
    control_plane: ControlPlane


def build_components(config_path, environ=None) -> Components:
    workspace = Path(__file__).resolve().parents[5]
    config = load_config(config_path, workspace, environ)
    cli = LarkCli(config.cli_candidates[0], identity=config.identity)
    publisher = Publisher(cli, config.target.root_token, config.target.space_id)
    tokens = publisher.resolve_control_plane()
    control_plane = ControlPlane(
        cli,
        {"index": tokens["index"], "lock": tokens["lock"]},
        lock_ttl_minutes=config.lock_ttl_minutes,
    )
    return Components(config, cli, publisher, control_plane)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate the Feishu knowledge store")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "resolve", "lock-status", "conflict-list"):
        command = commands.add_parser(name)
        command.add_argument("--config", required=True)
    for name in ("prepare", "publish", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--config", required=True)
        command.add_argument("--run-dir", required=True)
    conflict = commands.add_parser("conflict-append")
    conflict.add_argument("--config", required=True)
    conflict.add_argument("--key", required=True)
    conflict.add_argument("--reason", required=True)
    conflict.add_argument("--holder")
    fixture = commands.add_parser("lint-fixture")
    fixture.add_argument("--config", required=True)
    fixture.add_argument("--out", required=True)
    return parser


def export_lint_fixture(components: Components, out: str | Path) -> Path:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    config = components.config
    root = config.target.root_token
    nodes = [{"node_token": root, "title": "<root>", "parent_node_token": None}]

    def walk(parent_node_token: str) -> None:
        for node in components.cli.list_nodes(config.target.space_id, parent_node_token=parent_node_token):
            node_token = next(
                value for key in ("node_token", "obj_token", "token")
                if (value := node.get(key))
            )
            nodes.append({
                "node_token": node_token,
                "title": node.get("title", ""),
                "parent_node_token": parent_node_token,
            })
            walk(node_token)

    walk(root)
    (out / "tree.json").write_text(
        json.dumps(nodes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )

    def content(token: str) -> str:
        return components.cli.fetch_doc(token)["data"]["document"]["content"]

    tokens = components.publisher.resolve_control_plane()
    index_document = content(tokens["index"])
    (out / "index.md").write_text(index_document, encoding="utf-8")
    conflict_document = content(tokens["conflict"])
    (out / "conflict.md").write_text(conflict_document, encoding="utf-8")

    pages = out / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    index = components.control_plane.read_index()
    for entry in index.values():
        (pages / f"{entry.key.replace('/', '__')}.md").write_text(
            content(entry.doc_token), encoding="utf-8",
        )
    return out


def main(argv=None, stdout=None, components_factory=None) -> int:
    args = build_parser().parse_args(argv)
    stdout = stdout if stdout is not None else sys.stdout
    factory = components_factory if components_factory is not None else build_components
    try:
        components = factory(args.config)
        if args.command == "preflight":
            payload = components.cli.preflight(components.config.target.root_token)
        elif args.command == "resolve":
            payload = components.publisher.resolve_control_plane()
        elif args.command == "lock-status":
            revision_id, payload = components.control_plane.read_lock()
            payload = {"revision_id": revision_id, **payload}
        elif args.command == "conflict-append":
            if not args.holder:
                return 2
            tokens = components.publisher.resolve_control_plane()
            lease = components.control_plane.acquire_lock(
                args.holder, datetime.now(timezone.utc),
            )
            try:
                record = {"key": args.key, "reason": args.reason}
                components.publisher.append_conflict(tokens["conflict"], record)
            finally:
                components.control_plane.release_lock(lease)
            payload = record
        elif args.command == "prepare":
            runner = SyncRunner(
                args.config, components.cli, components.publisher,
                components.control_plane, config=components.config,
            )
            payload = str(runner.prepare(args.run_dir))
        elif args.command == "publish":
            runner = SyncRunner(
                args.config, components.cli, components.publisher,
                components.control_plane, config=components.config,
            )
            payload = runner.publish(args.run_dir)
        elif args.command == "verify":
            runner = SyncRunner(
                args.config, components.cli, components.publisher,
                components.control_plane, config=components.config,
            )
            payload = runner.verify(args.run_dir)
        elif args.command == "lint-fixture":
            payload = {"out": str(export_lint_fixture(components, args.out))}
        else:
            tokens = components.publisher.resolve_control_plane()
            payload = components.publisher.fetch_current(tokens["conflict"])
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=stdout)
        return 1
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
