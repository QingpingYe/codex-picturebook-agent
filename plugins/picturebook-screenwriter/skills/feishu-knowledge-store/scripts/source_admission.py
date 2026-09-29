"""Strict AI_KB_SOURCE_ADMISSION_V1 schema and subtree resolution."""

from dataclasses import dataclass
import json
from typing import Any, Mapping

ADMISSION_HEADING = "# AI_KB_SOURCE_ADMISSION_V1"
ADMISSION_FIELDS = ("token", "title", "decision", "decided_by", "decided_at", "reason")
ADMISSION_DECISIONS = ("admit", "exclude")


class SourceAdmissionError(ValueError):
    """The admission page exists but cannot be trusted."""


class DanglingAncestryError(SourceAdmissionError):
    def __init__(self, tokens):
        self.tokens = tuple(sorted(set(tokens)))
        super().__init__(
            "source admission ancestry is incomplete or cyclic: "
            + ", ".join(self.tokens)
        )


@dataclass(frozen=True)
class AdmissionEntry:
    token: str
    title: str
    decision: str
    decided_by: str
    decided_at: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "token": self.token,
            "title": self.title,
            "decision": self.decision,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AdmissionPolicy:
    entries: tuple[AdmissionEntry, ...] = ()
    revision_id: int | None = None
    page_present: bool = True


@dataclass(frozen=True)
class AdmissionResult:
    excluded_tokens: frozenset[str]
    excluded_containers: frozenset[str]
    included_tokens: frozenset[str]
    revision_id: int | None
    page_present: bool


def empty_admission_policy(page_present: bool = False,
                           revision_id: int | None = None) -> AdmissionPolicy:
    return AdmissionPolicy(entries=(), revision_id=revision_id,
                           page_present=page_present)


def _admission_entry(raw: Any) -> AdmissionEntry:
    if not isinstance(raw, Mapping) or set(raw) != set(ADMISSION_FIELDS):
        raise SourceAdmissionError("invalid admission entry fields")
    values = {field: raw[field] for field in ADMISSION_FIELDS}
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise SourceAdmissionError("admission entry values must be non-empty strings")
    if values["decision"] not in ADMISSION_DECISIONS:
        raise SourceAdmissionError("unknown admission decision")
    return AdmissionEntry(**values)


def parse_admission_payload(payload: Mapping[str, Any], *,
                            revision_id: int | None = None,
                            page_present: bool = True) -> AdmissionPolicy:
    if not isinstance(payload, Mapping):
        raise SourceAdmissionError("admission payload must be an object")
    if set(payload) != {"schema_version", "entries"} or payload.get("schema_version") != 1:
        raise SourceAdmissionError("invalid admission schema")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise SourceAdmissionError("admission entries must be a list")
    entries: list[AdmissionEntry] = []
    seen: set[str] = set()
    for raw in raw_entries:
        entry = _admission_entry(raw)
        if entry.token in seen:
            raise SourceAdmissionError("duplicate admission token")
        seen.add(entry.token)
        entries.append(entry)
    return AdmissionPolicy(tuple(entries), revision_id=revision_id,
                           page_present=page_present)


def parse_admission_snapshot(snapshot: Any) -> AdmissionPolicy:
    if snapshot is None:
        return empty_admission_policy(page_present=False)
    if not isinstance(snapshot, Mapping):
        raise SourceAdmissionError("admission snapshot must be an object")
    if set(snapshot) - {"schema_version", "entries", "revision_id", "page_present"}:
        raise SourceAdmissionError("invalid admission snapshot fields")
    page_present = snapshot.get("page_present", True)
    if not isinstance(page_present, bool):
        raise SourceAdmissionError("admission page_present must be boolean")
    revision_id = snapshot.get("revision_id")
    if revision_id is not None and (
        isinstance(revision_id, bool) or not isinstance(revision_id, int) or revision_id < 0
    ):
        raise SourceAdmissionError("admission revision_id is invalid")
    payload = {
        "schema_version": snapshot.get("schema_version"),
        "entries": snapshot.get("entries"),
    }
    return parse_admission_payload(payload, revision_id=revision_id,
                                   page_present=page_present)


def parse_source_admission(content: str) -> AdmissionPolicy:
    if not isinstance(content, str):
        raise SourceAdmissionError("admission control document must be text")
    prefix = ADMISSION_HEADING + "\n```json\n"
    suffix = "\n```\n"
    if (not content.startswith(prefix) or not content.endswith(suffix)
            or content.count("```") != 2):
        raise SourceAdmissionError("admission control document format is invalid")
    try:
        payload = json.loads(content[len(prefix):-len(suffix)])
    except json.JSONDecodeError as error:
        raise SourceAdmissionError("admission control JSON is invalid") from error
    return parse_admission_payload(payload)


def resolve_source_admission(policy: AdmissionPolicy,
                             snapshot: Mapping[str, Any]) -> AdmissionResult:
    if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("nodes"), Mapping):
        raise SourceAdmissionError("snapshot malformed: nodes must be a mapping")
    nodes = snapshot["nodes"]
    direct_excluded = {
        entry.token for entry in policy.entries if entry.decision == "exclude"
    }
    if not direct_excluded:
        return AdmissionResult(
            excluded_tokens=frozenset(),
            excluded_containers=frozenset(),
            included_tokens=frozenset(nodes),
            revision_id=policy.revision_id,
            page_present=policy.page_present,
        )

    parents = {
        token: ((node or {}).get("parent_node_token") or "")
        for token, node in nodes.items()
        if isinstance(node, dict)
    }
    closure = set(direct_excluded)
    dangling: list[str] = []
    for token in nodes:
        cursor = parents.get(token, "")
        seen = {token}
        while cursor:
            if cursor in seen:
                dangling.append(token)
                break
            if cursor in direct_excluded:
                closure.add(token)
                break
            if cursor not in parents:
                dangling.append(token)
                break
            seen.add(cursor)
            cursor = parents.get(cursor, "")
    if dangling:
        raise DanglingAncestryError(dangling)

    excluded_containers = {
        token for token in closure
        if isinstance(nodes.get(token), dict) and nodes[token].get("has_child") is True
    }
    included = set(nodes) - closure
    return AdmissionResult(
        excluded_tokens=frozenset(closure),
        excluded_containers=frozenset(excluded_containers),
        included_tokens=frozenset(included),
        revision_id=policy.revision_id,
        page_present=policy.page_present,
    )