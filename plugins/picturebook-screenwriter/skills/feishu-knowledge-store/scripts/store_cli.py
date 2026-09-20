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
    return parser


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
                components.control_plane,
            )
            payload = str(runner.prepare(args.run_dir))
        elif args.command == "publish":
            runner = SyncRunner(
                args.config, components.cli, components.publisher,
                components.control_plane,
            )
            payload = runner.publish(args.run_dir)
        elif args.command == "verify":
            runner = SyncRunner(
                args.config, components.cli, components.publisher,
                components.control_plane,
            )
            payload = runner.verify(args.run_dir)
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
