"""End-to-end Feishu knowledge synchronization runner."""

import json
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models import IndexEntry
from config import load_config
from runner_status import BootstrapState, RunStatus


class SyncRunnerError(RuntimeError):
    pass


@dataclass
class SyncReport:
    status: str
    candidates: int
    published: int
    preserved: int
    queued: int
    failed: int
    run_id: str
    source: dict[str, int]
    errors: list[str]

    def as_dict(self):
        return {
            "status": self.status,
            "candidates": self.candidates,
            "published": self.published,
            "preserved": self.preserved,
            "queued": self.queued,
            "failed": self.failed,
            "run_id": self.run_id,
            "source": self.source,
            "errors": self.errors,
        }


class SyncRunner:
    def __init__(self, config_path, cli, publisher=None, control_plane=None,
                 config=None) -> None:
        self.config_path = Path(config_path)
        self.cli = cli
        self.publisher = publisher
        self.control_plane = control_plane
        self._config = config
        self.bootstrap_state = BootstrapState.REQUIRED

    def prepare(self, run_dir) -> Path:
        run_dir = Path(run_dir)
        staging = run_dir / "wiki_staging"
        staging.mkdir(parents=True, exist_ok=True)
        config = self._load_config()
        source_nodes = self.cli.list_nodes(config.source.space_id)
        if config.source.root_mode == "node" and not source_nodes:
            raise SyncRunnerError("source root_mode=node returned no child nodes")
        self._write_json(run_dir / "source_nodes.json", source_nodes)

        manifest_path = staging / "_manifest.json"
        if not manifest_path.exists():
            raise SyncRunnerError("候选清单不存在，请先由 wiki-ingest 生成候选文件")
        return manifest_path

    def publish(self, run_dir) -> dict[str, Any]:
        run_dir = Path(run_dir)
        manifest_path = run_dir / "wiki_staging" / "_manifest.json"
        manifest = self._load_json(manifest_path)
        entries = manifest.get("entries", [])

        self.bootstrap_state = BootstrapState.IN_PROGRESS
        lease = self.control_plane.acquire_lock("sync-runner", datetime.now(timezone.utc))
        try:
            tokens = self.publisher.initialize()
            parent = tokens["content"]
            published = 0
            failed = 0
            errors = []
            successful_pages = 0
            for raw in entries:
                try:
                    entry = self._entry(raw)
                    body = self._candidate_body(run_dir, raw["path"])
                    published_entry = self.publisher.publish_new(entry, body, parent)
                    self.control_plane.update_index([published_entry])
                    published += 1
                    successful_pages += 1
                    if successful_pages % 5 == 0:
                        lease = self.control_plane.refresh_lock(
                            lease, datetime.now(timezone.utc)
                        )
                except Exception as error:
                    failed += 1
                    errors.append(f"{raw.get('key', '<unknown>')}: {error}")
            self.bootstrap_state = BootstrapState.COMPLETE
            if failed > 0:
                self.bootstrap_state = BootstrapState.FAILED
                raise SyncRunnerError("有候选页面发布失败")
            report = SyncReport(
                RunStatus.PUBLISHED, len(entries), published,
                preserved=0, queued=0, failed=failed,
                run_id=run_dir.name, source=self._source_summary(run_dir),
                errors=errors,
            )
            self._write_json(run_dir / "sync_report.json", report.as_dict())
            return report.as_dict()
        except Exception:
            self.bootstrap_state = BootstrapState.FAILED
            raise
        finally:
            self.control_plane.release_lock(lease)

    def verify(self, run_dir) -> dict[str, Any]:
        run_dir = Path(run_dir)
        manifest = self._load_json(run_dir / "wiki_staging" / "_manifest.json")
        report = {
            "status": RunStatus.VERIFIED,
            "candidates": len(manifest.get("entries", [])),
            "run_id": run_dir.name,
            "source": self._source_summary(run_dir),
        }
        self._write_json(run_dir / "verify_report.json", report)
        return report

    def _load_config(self) -> Any:
        if self._config is not None:
            return self._config
        return load_config(self.config_path, self.config_path.parent, {})

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

    @staticmethod
    def _source_summary(run_dir: Path) -> dict[str, int]:
        path = run_dir / "source_nodes.json"
        if not path.exists():
            return {"document_count": 0, "container_count": 0}
        nodes = json.loads(path.read_text(encoding="utf-8"))
        return {
            "document_count": len(nodes),
            "container_count": len([node for node in nodes
                                    if node.get("obj_type") in {"folder", "container"}]),
        }

    @staticmethod
    def _entry(raw: dict[str, Any]) -> IndexEntry:
        return IndexEntry(
            key=raw["key"],
            doc_token=raw.get("doc_token", raw["key"]),
            wiki_node_token=raw.get("wiki_node_token", raw["key"]),
            source_revisions=raw["source_revisions"],
            last_ai_revision_id=0,
            last_seen_revision_id=0,
            status="published",
        )

    @staticmethod
    def _candidate_body(run_dir: Path, relative_path: str) -> str:
        candidate_path = run_dir / "wiki_staging" / relative_path
        return candidate_path.read_text(encoding="utf-8")

    def _target_parent(self) -> str:
        return self._load_config().target.root_token

def main(argv=None):
    parser = argparse.ArgumentParser(description="Picture Book Feishu knowledge sync runner")
    parser.add_argument("command", choices=("prepare", "publish", "verify"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)
    runner = SyncRunner(args.config, None)
    if args.command == "prepare":
        result = runner.prepare(args.run_dir)
        print(result)
    elif args.command == "publish":
        print(runner.publish(args.run_dir))
    else:
        print(runner.verify(args.run_dir))


if __name__ == "__main__":
    main()
