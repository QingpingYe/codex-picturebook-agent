"""Remote, JSON-validated index and revision-protected synchronization lease."""

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from lark_cli import RevisionConflict
from models import IndexEntry
from remote_markdown import normalize_remote_markdown


class ControlPlaneCorrupt(ValueError):
    pass


class LockHeld(RuntimeError):
    pass


class LeaseOwnershipError(RuntimeError):
    pass


@dataclass(frozen=True)
class Lease:
    run_id: str
    holder: str
    started_at: datetime
    expires_at: datetime
    revision_id: int


class ControlPlane:
    def __init__(self, cli: Any, control_tokens: Mapping[str, str], lock_ttl_minutes: int = 45) -> None:
        if set(control_tokens) != {"index", "lock"} or any(not isinstance(token, str) or not token.strip() for token in control_tokens.values()):
            raise ValueError("control_tokens must contain non-empty index and lock tokens")
        if not isinstance(lock_ttl_minutes, int) or not 15 <= lock_ttl_minutes <= 120:
            raise ValueError("lock_ttl_minutes must be between 15 and 120")
        self.cli = cli
        self.control_tokens = dict(control_tokens)
        self.ttl = timedelta(minutes=lock_ttl_minutes)

    def read_index(self) -> dict[str, IndexEntry]:
        _revision, content = self._fetch(self.control_tokens["index"])
        payload = _parse_control(content, "# AI_KB_INDEX_V1")
        if set(payload) != {"schema_version", "entries"} or payload.get("schema_version") != 1 or not isinstance(payload.get("entries"), list):
            raise ControlPlaneCorrupt("invalid index schema")
        result: dict[str, IndexEntry] = {}
        for raw in payload["entries"]:
            entry = _index_entry(raw)
            if entry.key in result:
                raise ControlPlaneCorrupt("duplicate index key")
            result[entry.key] = entry
        return result

    def acquire_lock(self, holder: str, now: datetime) -> Lease:
        if not isinstance(holder, str) or not holder.strip():
            raise ValueError("holder must be non-empty")
        now = _utc(now)
        revision, payload = self._read_lock()
        if _lock_is_active(payload, now):
            raise LockHeld(_lock_message(payload))
        lease = self._new_lease(holder, now, revision)
        try:
            result = self.cli.update_doc(self.control_tokens["lock"], revision, _render_lock(lease))
            new_revision = self._revision_from_update_result(result)
            verified_revision, verified_payload = self._read_lock()
            if verified_payload["run_id"] != lease.run_id or verified_payload["holder"] != lease.holder:
                raise LockHeld(_lock_message(verified_payload))
            if verified_revision != new_revision:
                raise ControlPlaneCorrupt("lock revision did not advance as expected")
        except RevisionConflict:
            revision, payload = self._read_lock()
            if _lock_is_active(payload, now):
                raise LockHeld(_lock_message(payload))
            lease = self._new_lease(holder, now, revision)
            result = self.cli.update_doc(self.control_tokens["lock"], revision, _render_lock(lease))
            new_revision = self._revision_from_update_result(result)
            verified_revision, verified_payload = self._read_lock()
            if verified_payload["run_id"] != lease.run_id or verified_payload["holder"] != lease.holder:
                raise LockHeld(_lock_message(verified_payload))
            if verified_revision != new_revision:
                raise ControlPlaneCorrupt("lock revision did not advance as expected")
        return Lease(lease.run_id, lease.holder, lease.started_at, lease.expires_at, new_revision)

    def refresh_lock(self, lease: Lease, now: datetime) -> Lease:
        now = _utc(now)
        revision, payload = self._read_lock()
        self._assert_owner(payload, lease)
        renewed = Lease(lease.run_id, lease.holder, lease.started_at, now + self.ttl, revision)
        result = self.cli.update_doc(self.control_tokens["lock"], revision, _render_lock(renewed))
        new_revision = self._revision_from_update_result(result)
        return Lease(renewed.run_id, renewed.holder, renewed.started_at, renewed.expires_at, new_revision)

    def release_lock(self, lease: Lease) -> None:
        revision, payload = self._read_lock()
        self._assert_owner(payload, lease)
        empty = {"schema_version": 1, "run_id": None, "holder": None, "started_at": None, "expires_at": None}
        result = self.cli.update_doc(self.control_tokens["lock"], revision, _render_control("# AI_KB_LOCK_V1", empty))
        new_revision = self._revision_from_update_result(result)
        verified_revision, verified_payload = self._read_lock()
        if any(verified_payload[name] is not None for name in ("run_id", "holder", "started_at", "expires_at")):
            raise LeaseOwnershipError("lock was not released cleanly")
        if verified_revision != new_revision:
            raise ControlPlaneCorrupt("lock revision did not advance as expected")

    def rebuild_index(self, pages: list[Mapping[str, Any]]) -> dict[str, IndexEntry]:
        rebuilt: dict[str, IndexEntry] = {}
        for page in pages:
            try:
                metadata = page["metadata"]
                entry = _index_entry({
                    "key": metadata["key"], "doc_token": page["doc_token"], "wiki_node_token": page["wiki_node_token"],
                    "source_revisions": metadata["source_revisions"],
                    "last_ai_revision_id": metadata["last_ai_revision_id"], "last_seen_revision_id": page["revision_id"],
                    "status": "published",
                })
            except (KeyError, TypeError) as error:
                raise ControlPlaneCorrupt("published page metadata is invalid") from error
            if entry.key in rebuilt:
                raise ControlPlaneCorrupt("duplicate logical key during rebuild")
            rebuilt[entry.key] = entry
        return rebuilt

    def update_index(self, entries: Iterable[IndexEntry]) -> dict[str, IndexEntry]:
        merged = self.read_index()
        for entry in entries:
            merged[entry.key] = entry
        payload = {
            "schema_version": 1,
            "entries": [asdict(entry) for entry in sorted(merged.values(), key=lambda item: item.key)],
        }
        rendered = _render_control("# AI_KB_INDEX_V1", payload)
        revision, _ = self._fetch(self.control_tokens["index"])
        result = self.cli.update_doc(self.control_tokens["index"], revision, rendered)
        has_warnings = isinstance(result, Mapping) and result.get("warnings")
        is_partial = isinstance(result, Mapping) and result.get("data", {}).get("result") == "partial_success"
        if has_warnings or is_partial:
            raise ControlPlaneCorrupt("index update returned warnings or partial success")
        new_revision = self._revision_from_update_result(result)
        verified_revision, verified_content = self._fetch(self.control_tokens["index"])
        verified = _parse_control(verified_content, "# AI_KB_INDEX_V1")
        if verified_revision != new_revision or verified != payload:
            raise ControlPlaneCorrupt("index readback did not match the expected entries")
        return self.read_index()

    def _read_lock(self) -> tuple[int, dict[str, Any]]:
        revision, content = self._fetch(self.control_tokens["lock"])
        payload = _parse_control(content, "# AI_KB_LOCK_V1")
        _validate_lock(payload)
        return revision, payload

    def _fetch(self, token: str) -> tuple[int, str]:
        response = self.cli.fetch_doc(token)
        document = response.get("data", {}).get("document", response.get("document", response)) if isinstance(response, Mapping) else None
        if not isinstance(document, Mapping):
            raise ControlPlaneCorrupt("control document response is invalid")
        revision = document.get("revision_id")
        content = document.get("content", document.get("markdown"))
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0 or not isinstance(content, str):
            raise ControlPlaneCorrupt("control document revision or content is invalid")
        content = normalize_remote_markdown(content, kind="control")
        return revision, content

    def _new_lease(self, holder: str, now: datetime, revision: int) -> Lease:
        return Lease(uuid.uuid4().hex, holder, now, now + self.ttl, revision)

    @staticmethod
    def _revision_from_update_result(result: Any) -> int:
        if isinstance(result, int) and not isinstance(result, bool) and result >= 0:
            return result
        if isinstance(result, Mapping):
            document = result.get("data", {}).get("document", {})
            revision = document.get("revision_id")
            if not isinstance(revision, bool) and isinstance(revision, int) and revision >= 0:
                return revision
        raise ControlPlaneCorrupt("lock update did not return a valid revision_id")

    @staticmethod
    def _assert_owner(payload: Mapping[str, Any], lease: Lease) -> None:
        if payload["run_id"] != lease.run_id or payload["holder"] != lease.holder:
            raise LeaseOwnershipError("lock ownership no longer matches this lease")


def _parse_control(content: str, heading: str) -> dict[str, Any]:
    prefix = heading + "\n```json\n"
    suffix = "\n```\n"
    if not content.startswith(prefix) or not content.endswith(suffix) or content.count("```") != 2:
        raise ControlPlaneCorrupt("control document format is invalid")
    raw = content[len(prefix):-len(suffix)]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ControlPlaneCorrupt("control document JSON is invalid") from error
    if not isinstance(payload, dict):
        raise ControlPlaneCorrupt("control document JSON must be an object")
    return payload


def _index_entry(raw: Any) -> IndexEntry:
    fields = {"key", "doc_token", "wiki_node_token", "source_revisions", "last_ai_revision_id", "last_seen_revision_id", "status"}
    if not isinstance(raw, Mapping) or set(raw) != fields:
        raise ControlPlaneCorrupt("invalid index entry schema")
    for name in ("key", "doc_token", "wiki_node_token"):
        if not isinstance(raw[name], str) or not raw[name].strip():
            raise ControlPlaneCorrupt(f"invalid index entry {name}")
    if raw["key"].count("/") != 2:
        raise ControlPlaneCorrupt("invalid index entry key")
    revisions = raw["source_revisions"]
    if not isinstance(revisions, dict) or not revisions or any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip() for k, v in revisions.items()):
        raise ControlPlaneCorrupt("invalid index entry source_revisions")
    for name in ("last_ai_revision_id", "last_seen_revision_id"):
        if isinstance(raw[name], bool) or not isinstance(raw[name], int) or raw[name] < 0:
            raise ControlPlaneCorrupt(f"invalid index entry {name}")
    if raw["status"] not in ("published", "needs_review", "archived"):
        raise ControlPlaneCorrupt("invalid index entry status")
    return IndexEntry(**dict(raw))


def _validate_lock(payload: Mapping[str, Any]) -> None:
    fields = {"schema_version", "run_id", "holder", "started_at", "expires_at"}
    if set(payload) != fields or payload.get("schema_version") != 1:
        raise ControlPlaneCorrupt("invalid lock schema")
    values = (payload["run_id"], payload["holder"], payload["started_at"], payload["expires_at"])
    if all(value is None for value in values):
        return
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ControlPlaneCorrupt("invalid lock values")
    try:
        if _parse_time(payload["expires_at"]) <= _parse_time(payload["started_at"]):
            raise ControlPlaneCorrupt("lock expiry must follow start")
    except ValueError as error:
        raise ControlPlaneCorrupt("invalid lock timestamp") from error


def _lock_is_active(payload: Mapping[str, Any], now: datetime) -> bool:
    return payload["run_id"] is not None and _parse_time(payload["expires_at"]) > now


def _lock_message(payload: Mapping[str, Any]) -> str:
    return f"lock held by {payload['holder']} until {payload['expires_at']}"


def _render_lock(lease: Lease) -> str:
    return _render_control("# AI_KB_LOCK_V1", {
        "schema_version": 1, "run_id": lease.run_id, "holder": lease.holder,
        "started_at": _format_time(lease.started_at), "expires_at": _format_time(lease.expires_at),
    })


def _render_control(heading: str, payload: Mapping[str, Any]) -> str:
    return f"{heading}\n```json\n{json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}\n```\n"


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
