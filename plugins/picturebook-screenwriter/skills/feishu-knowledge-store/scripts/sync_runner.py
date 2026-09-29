"""End-to-end Feishu knowledge synchronization runner."""

import json
import argparse
from dataclasses import dataclass, replace, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from control_plane import ControlPlaneCorrupt, IndexOutcomeUnknown
from models import IndexEntry
from config import load_config
from config_paths import resolve_config_path
from runner_status import BootstrapState, RunStatus
from page_codec import Candidate, RemotePage, parse_candidate, parse_remote_page, render_remote_page
from publisher import NeedsReview
from remote_markdown import mark_insensitive
from lark_cli import RevisionConflict


class SyncRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Decision:
    action: str
    reason: str
    candidate_markdown: str | None = None


@dataclass(frozen=True)
class _CandidateEntry(IndexEntry):
    body: str = ""


@dataclass(frozen=True)
class _RemoteSnapshot(RemotePage):
    revision_id: int = 0


def _decide(candidate: Candidate, current: RemotePage) -> _Decision:
    if current.has_non_roundtrippable_content:
        return _Decision("needs_review", "document contains resources or comments")
    if mark_insensitive(candidate.body) == mark_insensitive(current.body):
        return _Decision("preserve", "content unchanged")
    return _Decision("publish", "content changed", candidate.body)


def _try_recover_index(indexed: IndexEntry, candidate: IndexEntry,
                       current: RemotePage) -> IndexEntry | None:
    metadata = current.metadata
    revision = getattr(current, "revision_id", None)
    body = getattr(candidate, "body", None)
    if (indexed.status == "archived" or current.has_non_roundtrippable_content
            or metadata["key"] != indexed.key or candidate.key != indexed.key
            or metadata["last_ai_revision_id"] <= indexed.last_ai_revision_id
            or revision is None or revision < metadata["last_ai_revision_id"]
            or metadata["source_revisions"] != candidate.source_revisions
            or body is None or mark_insensitive(current.body) != mark_insensitive(body)):
        return None
    return replace(indexed, source_revisions=dict(metadata["source_revisions"]),
                   source_edit_times=metadata.get("source_edit_times"),
                   last_ai_revision_id=metadata["last_ai_revision_id"],
                   last_seen_revision_id=revision, status="published")


@dataclass
class SyncReport:
    status: str
    candidates: int
    published: int
    preserved: int
    failed: int
    run_id: str
    source: dict[str, int]
    errors: list[str]
    retried: int = 0
    overwritten_human_edits: list[str] = field(default_factory=list)
    pages: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self):
        result = asdict(self)
        result["overwritten_human_edits"] = sorted(set(self.overwritten_human_edits))
        return result


class SyncRunner:
    def __init__(self, config_path, cli, publisher=None, control_plane=None,
                 config=None, workspace=None, environ=None) -> None:
        self.config_path = Path(config_path) if config_path is not None else None
        self.workspace = Path(workspace) if workspace is not None else Path.cwd()
        self.environ = environ
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

    MAX_RETRIES = 2

    def publish(self, run_dir) -> dict[str, Any]:
        run_dir = Path(run_dir)
        try:
            entries = self._load_json(run_dir / "wiki_staging" / "_manifest.json").get("entries", [])
            source_summary = self._source_summary(run_dir)
        except Exception as error:
            # The every-failed-run report contract also covers unusable run input.
            run_dir.mkdir(parents=True, exist_ok=True)
            report = SyncReport("failed", 0, 0, 0, 1, run_dir.name, {},
                                [f"invalid run input: {error}"])
            self._write_json(run_dir / "sync_report.json", report.as_dict())
            raise SyncRunnerError(f"invalid run input: {error}") from error
        report = SyncReport("published", len(entries), 0, 0, 0, run_dir.name,
                            source_summary, [])
        lease = None
        self.bootstrap_state = BootstrapState.IN_PROGRESS
        try:
            lease = self.control_plane.acquire_lock("sync-runner", datetime.now(timezone.utc))
            remote_index = self.control_plane.read_index()
            parent = self.publisher.initialize()["content"]
            for position, raw in enumerate(entries):
                key = raw.get("key", "<unknown>")
                indexed = remote_index.get(key)
                page = dict(key=key, action="needs_review", reason="", before_revision=None,
                            after_revision=None, page_overwritten=False, index_committed=False)
                report.pages.append(page)
                attempted_write = False
                attempted_index = False
                stop = False
                try:
                    candidate, body = self._candidate(raw, run_dir)
                    if indexed and indexed.status == "archived":
                        raise NeedsReview("archived entry requires governance")
                    self._check_times(indexed, candidate)
                    for attempt in range(self.MAX_RETRIES + 1):
                        if indexed is None:
                            page.update(action="publish", reason="first publish")
                            if candidate.source_edit_times is None:
                                page["reason"] += "; source time baseline missing"
                            attempted_write = True
                            result = self.publisher.publish_new(candidate, body, parent)
                            break
                        current = self.publisher.fetch_current(indexed.doc_token)
                        parsed = parse_remote_page(current["content"])
                        snapshot = _RemoteSnapshot(parsed.body, parsed.metadata,
                                                   parsed.has_non_roundtrippable_content, current["revision_id"])
                        page["before_revision"] = current["revision_id"]
                        result = _try_recover_index(indexed, candidate, snapshot)
                        if result is not None:
                            self._check_times(indexed, result)
                            page.update(action="preserve", reason="recovered remote page/index handoff",
                                        after_revision=current["revision_id"])
                            break
                        self._assert_page_matches_index(indexed, snapshot)
                        decision = _decide(Candidate(body, {}), snapshot)
                        page.update(action=decision.action, reason=decision.reason)
                        if decision.action == "needs_review":
                            raise NeedsReview(decision.reason)
                        if decision.action == "preserve":
                            times = indexed.source_edit_times
                            if set(candidate.source_revisions) != set(indexed.source_revisions):
                                page["reason"] += "; source topology pending"
                            elif candidate.source_edit_times is not None:
                                times = candidate.source_edit_times
                            result = replace(indexed, last_seen_revision_id=current["revision_id"],
                                             source_edit_times=times, status="published")
                            page["after_revision"] = current["revision_id"]
                            break
                        times = candidate.source_edit_times
                        if times is None and candidate.source_revisions == indexed.source_revisions:
                            times = indexed.source_edit_times
                        if times is None:
                            page["reason"] += "; source time baseline missing"
                        candidate = replace(candidate, source_edit_times=times)
                        metadata = self._metadata(candidate)
                        attempted_write = True
                        try:
                            result = self.publisher.conditional_update(
                                indexed, current, render_remote_page(body, metadata),
                                candidate.source_revisions, source_edit_times=times)
                            break
                        except (RevisionConflict, NeedsReview) as error:
                            if getattr(error, "page_written", False):
                                # The candidate body is already confirmed on the page.
                                # Retrying with a stale revision is unsafe, so disclose
                                # the overwrite and stop this page.
                                page["page_overwritten"] = True
                                page["after_revision"] = getattr(error, "after_revision", None)
                                if indexed and page["before_revision"] > indexed.last_seen_revision_id:
                                    report.overwritten_human_edits.append(key)
                                raise NeedsReview(
                                    "confirmed page write followed by a failed metadata correction"
                                ) from error
                            conflict = isinstance(error, RevisionConflict) or isinstance(error.__cause__, RevisionConflict)
                            if not conflict:
                                raise
                            attempted_write = False
                            if attempt == self.MAX_RETRIES:
                                raise NeedsReview("revision retries exhausted") from error
                            report.retried += 1
                    if attempted_write:
                        page.update(page_overwritten=True, after_revision=result.last_seen_revision_id)
                        if indexed and page["before_revision"] > indexed.last_seen_revision_id:
                            report.overwritten_human_edits.append(key)
                    result = IndexEntry(**{name: getattr(result, name) for name in IndexEntry.__dataclass_fields__})
                    attempted_index = True
                    self.control_plane.update_index([result])
                    try:
                        readback = self.control_plane.read_index().get(key)
                    except Exception as read_error:
                        raise IndexOutcomeUnknown("index readback unavailable after update") from read_error
                    if readback != result:
                        raise IndexOutcomeUnknown("index readback did not match")
                    page["index_committed"] = True
                    remote_index[key] = result
                    if page["action"] == "publish":
                        report.published += 1
                    else:
                        report.preserved += 1
                    if (report.published + report.preserved) % 5 == 0:
                        lease = self.control_plane.refresh_lock(lease, datetime.now(timezone.utc))
                except Exception as error:
                    report.failed += 1
                    page.update(action="needs_review", reason=str(error))
                    report.errors.append(f"{key}: {error}")
                    # A call that may have written cannot safely be repeated in this run.
                    if attempted_write and not page["page_overwritten"]:
                        known_unwritten = "logical key page already exists" in str(error)
                        page["page_overwritten"] = False if known_unwritten else None
                        stop = not known_unwritten
                    if attempted_index:
                        page["index_committed"] = None if isinstance(error, IndexOutcomeUnknown) else False
                        stop = True
                    if page["page_overwritten"] is True and page["index_committed"] is not True:
                        # A confirmed overwrite without a committed index: remaining
                        # candidates must not be reported as normal publications.
                        stop = True
                    if indexed and indexed.status != "archived" and not stop:
                        try:
                            review = replace(indexed, status="needs_review")
                            self.control_plane.update_index([review])
                            if self.control_plane.read_index().get(key) != review:
                                raise SyncRunnerError("review status readback did not match")
                            remote_index[key] = review
                        except Exception as status_error:
                            report.errors.append(f"{key}: needs_review status not committed: {status_error}")
                            stop = True
                    if stop:
                        for pending in entries[position + 1:]:
                            report.errors.append(f"{pending.get('key', '<unknown>')}: unprocessed after uncertain page/index handoff")
                        break
        except Exception as error:
            report.failed += 1
            report.errors.append(str(error))
            processed = {page["key"] for page in report.pages}
            report.errors.extend(f"{raw.get('key', '<unknown>')}: unprocessed" for raw in entries
                                 if raw.get("key") not in processed)
        finally:
            if lease is not None:
                try:
                    self.control_plane.release_lock(lease)
                except Exception as error:
                    report.failed += 1
                    report.errors.append(f"lease release failed: {error}")
            report.status = "failed" if report.failed else "published"
            self.bootstrap_state = BootstrapState.FAILED if report.failed else BootstrapState.COMPLETE
            self._write_json(run_dir / "sync_report.json", report.as_dict())
        if report.failed:
            raise SyncRunnerError("候选页面发布失败；详见 sync_report.json")
        return report.as_dict()

    @staticmethod
    def _check_times(indexed, candidate):
        if indexed and indexed.source_edit_times and candidate.source_edit_times:
            if any(value < indexed.source_edit_times[token]
                   for token, value in candidate.source_edit_times.items()
                   if token in indexed.source_edit_times):
                raise NeedsReview("stale candidate source edit time")

    @staticmethod
    def _metadata(candidate):
        metadata = dict(schema_version=1, key=candidate.key,
                        page_type=candidate.key.split("/")[-1],
                        source_node_tokens=sorted(candidate.source_revisions),
                        source_revisions=candidate.source_revisions, last_ai_revision_id=0)
        if candidate.source_edit_times is not None:
            metadata["source_edit_times"] = candidate.source_edit_times
        return metadata

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
        if self.config_path is None:
            resolved = resolve_config_path(None, self.workspace, self.environ)
            self.config_path = resolved.path
        return load_config(self.config_path, self.workspace, self.environ)

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
    def _candidate(raw: dict[str, Any], run_dir: Path):
        body = SyncRunner._candidate_body(run_dir, raw["path"])
        parsed = parse_candidate(body)
        if parsed.metadata["key"] != raw["key"]:
            raise ValueError(f"candidate logical key mismatch: {raw['key']}")
        entry = _CandidateEntry(
            key=raw["key"], doc_token=raw.get("doc_token", raw["key"]),
            wiki_node_token=raw.get("wiki_node_token", raw["key"]),
            source_revisions=parsed.metadata["source_revisions"],
            last_ai_revision_id=0, last_seen_revision_id=0, status="published",
            source_edit_times=parsed.metadata.get("source_edit_times"), body=parsed.body,
        )
        return entry, parsed.body

    @staticmethod
    def _candidate_body(run_dir: Path, relative_path: str) -> str:
        candidate_path = run_dir / "wiki_staging" / relative_path
        return candidate_path.read_text(encoding="utf-8")

    @staticmethod
    def _assert_page_matches_index(indexed: IndexEntry, current: _RemoteSnapshot):
        metadata = current.metadata
        if (metadata["key"] != indexed.key
                or metadata["source_revisions"] != indexed.source_revisions
                or metadata["last_ai_revision_id"] != indexed.last_ai_revision_id
                or current.revision_id < max(indexed.last_ai_revision_id, indexed.last_seen_revision_id)):
            raise NeedsReview("remote page metadata does not match the remote index")

    def _target_parent(self) -> str:
        return self._load_config().target.root_token

def main(argv=None):
    parser = argparse.ArgumentParser(description="Picture Book Feishu knowledge sync runner")
    parser.add_argument("command", choices=("prepare", "publish", "verify"))
    parser.add_argument("--config")
    parser.add_argument("--workspace")
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)
    # This module is the runner implementation. Only store_cli.py wires the real
    # lark-cli client, publisher and control plane, so a direct invocation here
    # would fail at the first remote call; refuse it with a usable pointer.
    raise SystemExit(
        "sync_runner.py is not an operator entry point; run "
        "store_cli.py prepare/publish/verify --config <config> --run-dir <run_dir> "
        f"(requested: {args.command})."
    )


if __name__ == "__main__":
    main()
