"""Read-only projection of AI_KB_INDEX_V1 and source admission policy."""

from dataclasses import replace
import json
import os
from pathlib import Path
from typing import Any, Mapping

from source_admission import (
    AdmissionPolicy,
    SourceAdmissionError,
    empty_admission_policy,
    parse_source_admission,
)


def _admission_payload(policy: AdmissionPolicy | None) -> dict[str, Any]:
    policy = policy or empty_admission_policy(page_present=False)
    return {
        "schema_version": 1,
        "revision_id": policy.revision_id,
        "page_present": policy.page_present,
        "entries": [entry.as_dict() for entry in policy.entries],
    }


def build_source_baseline(index_revision_id: int, entries: Mapping[str, Any],
                          admission: AdmissionPolicy | None) -> dict[str, Any]:
    if isinstance(index_revision_id, bool) or not isinstance(index_revision_id, int) or index_revision_id < 0:
        raise ValueError("index_revision_id must be a non-negative integer")
    projected = []
    for key in sorted(entries):
        entry = entries[key]
        source_revisions = dict(entry.source_revisions)
        source_edit_times = (
            None if entry.source_edit_times is None
            else dict(entry.source_edit_times)
        )
        projected.append({
            "key": entry.key,
            "source_revisions": source_revisions,
            "source_edit_times": source_edit_times,
        })
    return {
        "schema_version": 1,
        "index_revision_id": index_revision_id,
        "entries": projected,
        "admission": _admission_payload(admission),
    }


def read_source_admission(cli: Any, token: str | None) -> AdmissionPolicy:
    if not token:
        return empty_admission_policy(page_present=False)
    document = cli.fetch_doc(token).get("data", {}).get("document", {})
    revision = document.get("revision_id")
    content = document.get("content")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise SourceAdmissionError("admission page has invalid revision")
    if not isinstance(content, str) or not content:
        raise SourceAdmissionError("admission page is empty or unreadable")
    return replace(parse_source_admission(content), revision_id=revision, page_present=True)


def write_source_baseline(path: str | Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    tmp = Path(str(path) + ".tmp")
    with tmp.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    os.replace(tmp, path)