"""Refuse an authority evidence bundle that cannot be read as one.

Every operation entry point consumes the bundle the knowledge loader writes, so
the shape check lives here rather than inside one CLI: two copies would drift,
and the copy that reads less is the one that pays for a call it cannot use. The
checked fields are exactly the ones the bundle's readers touch; "present" means
the key is there, because the readers default only a *missing* key.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_RUNTIME_SCRIPTS = Path(__file__).resolve().parents[2] / "jev-decision-runtime" / "scripts"
if str(_RUNTIME_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_SCRIPTS))

from decision_contract import ContractError  # noqa: E402


def is_text_list(value: Any) -> bool:
    """True when `value` is a list-like of text, never a bare string.

    A bare string would be silently read one character at a time, which is how
    `warnings: "abc"` turned into three warnings on the way to disk, and a
    present `null` defeats `payload.get("warnings", ())`, so neither counts as
    an absent field.
    """

    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        return False
    return all(isinstance(entry, str) for entry in value)


def is_revision_vector(value: Any) -> bool:
    """True when `value` maps node tokens to revision ids, all of them text.

    A list of pairs is not one: `dict()` would accept it, so nothing downstream
    would notice, and a present `null` is not one either.
    """

    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Mapping):
        return False
    return all(
        isinstance(node, str) and isinstance(revision, str)
        for node, revision in value.items()
    )


def require_bundle_shape(bundle: Any) -> None:
    """Refuse a file that cannot be screened as an authority evidence bundle.

    Only the fields the readers touch are checked, and nothing else in the file
    is rewritten: the top level must be a JSON object, `items` must be a list of
    evidence objects, and every item must carry a non-empty text `key` and its
    page body as a text `content`. Two further fields are read from the same
    file and so are checked too: `source_revisions`, when present, must be an
    object of text revisions, because a page's version vector is copied into the
    request context and into the written bundles; and `warnings`, when present,
    must be a list of text, because the relevance screen carries it into the
    reduced context bundle. "Present" means the key is there: neither field has
    a `null` exemption, because the readers default only a *missing* key
    (`payload.get("warnings", ())`), so a `null` would still raise once the
    screening call had been paid for. Every other key, including ones this check
    never looks at, is passed through untouched.

    Reading such a file used to end in an `AttributeError`/`TypeError`
    traceback once an output flag was set, and in a successful-looking report of
    an empty screen when none was: the caller could not tell a screen of nothing
    from a bundle that was never read.
    """

    if not isinstance(bundle, Mapping):
        raise ContractError(
            "bundle must be a JSON object of evidence items, "
            f"got {type(bundle).__name__}"
        )
    items = bundle.get("items")
    if isinstance(items, (str, bytes, bytearray)) or not isinstance(items, Sequence):
        raise ContractError("bundle must carry an `items` list of evidence objects")
    if "warnings" in bundle and not is_text_list(bundle["warnings"]):
        raise ContractError("bundle `warnings` must be a list of text warnings")
    for position, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise ContractError(f"bundle item {position} must be an evidence object")
        if not isinstance(item.get("content"), str):
            raise ContractError(
                f"bundle item {position} must carry its page body as a text `content`"
            )
        if not isinstance(item.get("key"), str) or not item["key"]:
            raise ContractError(
                f"bundle item {position} must carry a non-empty text `key`"
            )
        if "source_revisions" in item and not is_revision_vector(
            item["source_revisions"]
        ):
            raise ContractError(
                f"bundle item {position} `source_revisions` must be an object of "
                "text revisions"
            )
