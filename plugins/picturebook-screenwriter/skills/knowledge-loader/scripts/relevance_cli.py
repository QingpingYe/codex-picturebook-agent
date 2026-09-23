"""CLI for the knowledge_relevance screening operation."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# The operation is composed from two skills: this CLI lives with the knowledge
# side but drives the shared decision runtime, so both script directories join
# the import path before the sibling modules are imported.
SCRIPT_DIR = Path(__file__).resolve().parent
RUNTIME_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "jev-decision-runtime" / "scripts"
)
for scripts_path in (RUNTIME_SCRIPTS, SCRIPT_DIR):
    if str(scripts_path) not in sys.path:
        sys.path.insert(0, str(scripts_path))

from decision_contract import (  # noqa: E402
    ContractError,
    default_policy_path,
    is_valid_run_id,
    load_policy,
)
from jev_client import JevClient, JevClientError, UrllibTransport  # noqa: E402
from jev_runner import (  # noqa: E402
    AmbiguousAttempt,
    LeaseHeld,
    NoPendingCall,
    RunnerConfig,
    reject_credential_arguments,
    write_atomic,
)
from relevance import (  # noqa: E402
    OPERATION,
    dependency_bundle,
    filtered_bundle,
    screen_candidates,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="relevance_cli.py")
    parser.add_argument("--bundle", required=True, help="authority evidence bundle JSON")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--artifact-type", required=True)
    parser.add_argument("--task", required=True, help="current stage description")
    parser.add_argument("--brief", default="", help="the approved brief, if any")
    parser.add_argument("--terms", default="", help="comma separated recall terms")
    parser.add_argument("--declared-page-type", action="append", default=[])
    parser.add_argument("--declared-key", action="append", default=[])
    parser.add_argument("--filtered-out", help="write the reduced context bundle here")
    parser.add_argument("--dependency-out", help="write the unfiltered lock bundle here")
    parser.add_argument("--run-id", help="override the run directory name as the run id")
    parser.add_argument("--policy")
    return parser


def _split_terms(raw: str) -> tuple[str, ...]:
    return tuple(term.strip() for term in raw.split(",") if term.strip())


def _is_text_list(value: Any) -> bool:
    """True when `value` is a list-like of text, never a bare string.

    A bare string would be silently read one character at a time, which is how
    `warnings: "abc"` turned into three warnings on the way to disk, and a
    present `null` defeats `payload.get("warnings", ())`, so neither counts as
    an absent field.
    """

    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        return False
    return all(isinstance(entry, str) for entry in value)


def _is_revision_vector(value: Any) -> bool:
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


def _require_bundle_shape(bundle: Any) -> None:
    """Refuse a file that cannot be screened as an authority evidence bundle.

    Only the fields this CLI reads are checked, and nothing else in the file is
    rewritten: the top level must be a JSON object, `items` must be a list of
    evidence objects, and every item must carry a non-empty text `key` and its
    page body as a text `content`. Two further fields are read from the same
    file and so are checked too: `source_revisions`, when present, must be an
    object of text revisions, because a page's version vector is copied into the
    request context and into the written bundles; and `warnings`, when present,
    must be a list of text, because it is carried into the reduced context
    bundle. "Present" means the key is there: neither field has a `null`
    exemption, because the readers default only a *missing* key
    (`payload.get("warnings", ())`), so a `null` would still raise once the
    screening call had been paid for. Every other key, including ones this CLI
    never looks at, is passed through untouched.

    Reading such a file used to end in an `AttributeError`/`TypeError`
    traceback once an output flag was set, and in a successful-looking report of
    an empty screen (`required_count = 0`, `candidate_count = 0`) when none was:
    the caller could not tell a screen of nothing from a bundle that was never
    read.
    """

    if not isinstance(bundle, Mapping):
        raise ContractError(
            "bundle must be a JSON object of evidence items, "
            f"got {type(bundle).__name__}"
        )
    items = bundle.get("items")
    if isinstance(items, (str, bytes, bytearray)) or not isinstance(items, Sequence):
        raise ContractError("bundle must carry an `items` list of evidence objects")
    if "warnings" in bundle and not _is_text_list(bundle["warnings"]):
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
        if "source_revisions" in item and not _is_revision_vector(
            item["source_revisions"]
        ):
            raise ContractError(
                f"bundle item {position} `source_revisions` must be an object of "
                "text revisions"
            )


def _resolve_run_id(args: argparse.Namespace) -> str:
    """Name the run, defaulting to the run directory but never past the contract.

    A directory name is the convention every other runner CLI in this plugin
    follows, but it is not guaranteed to be a legal `run_id`: dots, spaces, CJK,
    or an over-long name all fail the request contract. An unusable default is
    refused here, where the message can name `--run-id`, instead of surfacing
    later as a request-contract error.
    """

    if args.run_id:
        if not is_valid_run_id(args.run_id):
            raise ContractError(
                f"--run-id {args.run_id!r} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,127}}"
            )
        return args.run_id
    derived = Path(args.run_dir).resolve().name
    if not is_valid_run_id(derived):
        raise ContractError(
            f"run directory name {derived!r} is not a legal run id "
            "(it must match [A-Za-z0-9][A-Za-z0-9_-]{0,127}); "
            "pass --run-id to name the run explicitly"
        )
    return derived


def main(argv=None, environ=None, transport_factory=None, stdout=None, stderr=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr

    refusal = reject_credential_arguments(arguments)
    if refusal is not None:
        print(refusal, file=err)
        return 2

    parser = _build_parser()
    if not arguments:
        parser.print_usage(err)
        return 2
    try:
        args = parser.parse_args(arguments)
    except SystemExit as exit_error:
        return 2 if exit_error.code else 0

    try:
        run_id = _resolve_run_id(args)
        bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
        _require_bundle_shape(bundle)
        policy = load_policy(args.policy or default_policy_path())
        config = RunnerConfig(run_dir=Path(args.run_dir), policy=policy)
        factory = transport_factory or UrllibTransport
        client = JevClient(factory(), environ=environ)
        outcome = screen_candidates(
            run_id=run_id,
            policy=policy,
            artifact_type=args.artifact_type,
            task_description=args.task,
            brief=args.brief,
            bundle=bundle,
            config=config,
            client=client,
            declared_page_types=tuple(args.declared_page_type),
            declared_keys=tuple(args.declared_key),
            terms=_split_terms(args.terms),
        )
        # Both bundles are written inside the guard, with the runtime's atomic
        # writer: an unwritable path must reach the documented JSON error
        # instead of losing the whole report to a traceback after the screening
        # call has already been paid for.
        if args.filtered_out:
            write_atomic(args.filtered_out, filtered_bundle(bundle, outcome))
        if args.dependency_out:
            write_atomic(args.dependency_out, dependency_bundle(bundle))
    except (ContractError, JevClientError, NoPendingCall, LeaseHeld, AmbiguousAttempt) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False), file=err)
        return 1
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False), file=err)
        return 1

    print(json.dumps({
        "schema_version": "pb-relevance-report-v1",
        "operation": OPERATION,
        "run_id": run_id,
        "required_count": outcome.required_count,
        "candidate_count": outcome.candidate_count,
        "kept_chunk_ids": [chunk.chunk_id for chunk in outcome.kept],
        "conflict_chunk_ids": [chunk.chunk_id for chunk in outcome.conflicts],
        "uncertain_chunk_ids": [chunk.chunk_id for chunk in outcome.uncertain],
        "excluded_soft": list(outcome.excluded_soft),
        "routes": list(outcome.routes),
        "results": list(outcome.results),
    }, ensure_ascii=False, sort_keys=True), file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
