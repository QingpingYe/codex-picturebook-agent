"""CLI for the two-path comparison report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from comparison import build_case_report, report_to_markdown
from decision_contract import ContractError, default_policy_path, load_policy
from jev_client import JevClientError
from jev_runner import reject_credential_arguments
from telemetry import now_iso

# The session-export helper owns the audit bundle layout; this CLI composes it
# rather than duplicating the format.
_SESSION_EXPORT_SCRIPTS = Path(__file__).resolve().parents[2] / "session-export" / "scripts"
if str(_SESSION_EXPORT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SESSION_EXPORT_SCRIPTS))

from export_session import export_session  # noqa: E402

OUT_OF_PLUGIN_ERROR = "output directory must be outside the plugin"
SESSION_ID = "path-comparison"


def require_explicit_output_dir(output_dir, plugin_root) -> Path:
    """Validate a user-supplied export directory.

    The two rules mirror session-export: the caller must name the directory
    absolutely, and it must not land inside the installed plugin.
    """

    output_dir = Path(output_dir)
    if not output_dir.is_absolute():
        raise ValueError("output directory must be an absolute path")
    resolved = output_dir.resolve()
    plugin_root = Path(plugin_root).resolve()
    if resolved == plugin_root or plugin_root in resolved.parents:
        raise ValueError(OUT_OF_PLUGIN_ERROR)
    return resolved


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="compare_cli.py")
    parser.add_argument("--run-dir", required=True,
                        help="the jev_assisted run directory holding the traces")
    parser.add_argument("--llm-usage", required=True,
                        help="the plain-LLM usage entry, read out of CC Switch by hand")
    parser.add_argument("--output-dir",
                        help="absolute directory to export into; omit to only print")
    parser.add_argument("--plugin-root", help="plugin install root, for the boundary check")
    parser.add_argument("--policy")
    return parser


def main(argv=None, stdout=None, stderr=None) -> int:
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
        policy = load_policy(args.policy or default_policy_path())
        report = build_case_report(
            run_dir=Path(args.run_dir), llm_usage_path=Path(args.llm_usage),
            policy=policy,
        )
        if args.output_dir:
            if not args.plugin_root:
                raise ValueError("--plugin-root is required when exporting")
            bundle = _export(args, report)
            report["exported_to"] = str(bundle)
    except (ContractError, JevClientError, OSError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False),
              file=err)
        return 1

    print(json.dumps(report, ensure_ascii=False, sort_keys=True), file=out)
    return 0


def _export(args, report) -> Path:
    """Write the report through the session-export bundle helper."""

    output_dir = require_explicit_output_dir(args.output_dir, args.plugin_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle = export_session(
        SESSION_ID, [], ["comparison.json", "comparison.md"], [],
        output_dir, Path(args.plugin_root),
    )
    (bundle / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (bundle / "comparison.md").write_text(
        report_to_markdown(report) + f"\n生成时间：{now_iso()}\n", encoding="utf-8"
    )
    return bundle


if __name__ == "__main__":
    raise SystemExit(main())
