"""CLI for the knowledge_relevance screening operation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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

from decision_contract import ContractError, default_policy_path, load_policy  # noqa: E402
from jev_client import JevClient, JevClientError, UrllibTransport  # noqa: E402
from jev_runner import (  # noqa: E402
    AmbiguousAttempt,
    LeaseHeld,
    NoPendingCall,
    RunnerConfig,
    reject_credential_arguments,
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
    parser.add_argument("--policy")
    return parser


def _split_terms(raw: str) -> tuple[str, ...]:
    return tuple(term.strip() for term in raw.split(",") if term.strip())


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
        bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
        policy = load_policy(args.policy or default_policy_path())
        config = RunnerConfig(run_dir=Path(args.run_dir), policy=policy)
        factory = transport_factory or UrllibTransport
        client = JevClient(factory(), environ=environ)
        # The run directory is named after the run id, which is how every other
        # runner CLI in this plugin resolves it.
        run_id = Path(args.run_dir).resolve().name
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
    except (ContractError, JevClientError, NoPendingCall, LeaseHeld, AmbiguousAttempt) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False), file=err)
        return 1
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False), file=err)
        return 1

    if args.filtered_out:
        Path(args.filtered_out).write_text(
            json.dumps(filtered_bundle(bundle, outcome), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.dependency_out:
        Path(args.dependency_out).write_text(
            json.dumps(dependency_bundle(bundle), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

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
