"""CLI for the two-path comparison report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from comparison import build_case_report, report_to_markdown
from decision_contract import ContractError, default_policy_path, load_policy
from jev_client import JevClientError
from jev_runner import parse_known_options, reject_credential_arguments
from telemetry import now_iso

# The session-export helper owns the audit bundle layout; this CLI composes it
# rather than duplicating the format.
_SESSION_EXPORT_SCRIPTS = Path(__file__).resolve().parents[2] / "session-export" / "scripts"
if str(_SESSION_EXPORT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SESSION_EXPORT_SCRIPTS))

from export_session import export_session  # noqa: E402

OUT_OF_PLUGIN_ERROR = "output directory must be outside the plugin"
SESSION_ID = "path-comparison"

# The gate may not trust the caller's `--plugin-root` alone: a decoy root would
# let an export land inside the plugin this file is installed in. The script
# always knows its own plugin root, so both are checked.
PLUGIN_ROOT = Path(__file__).resolve().parents[3]


def require_explicit_output_dir(output_dir, plugin_root) -> Path:
    """Validate a user-supplied export directory.

    The two rules mirror session-export: the caller must name the directory
    absolutely, and it must not land inside the installed plugin. "The
    installed plugin" means both the root the caller declares and the root this
    file actually lives in.
    """

    output_dir = Path(output_dir)
    if not output_dir.is_absolute():
        raise ValueError("output directory must be an absolute path")
    resolved = output_dir.resolve()
    for root in (Path(plugin_root).resolve(), Path(PLUGIN_ROOT).resolve()):
        if resolved == root or root in resolved.parents:
            raise ValueError(OUT_OF_PLUGIN_ERROR)
    return resolved


class _StreamArgumentParser(argparse.ArgumentParser):
    """An ArgumentParser that reports to the streams this CLI was handed.

    argparse writes usage and error text to the process streams by default,
    which would make the `stderr` argument a lie: an embedded caller would
    capture nothing while the message leaked to the terminal.
    """

    def __init__(self, *args, stream=None, **kwargs):
        self._stream = stream
        super().__init__(*args, **kwargs)

    def _print_message(self, message, file=None):
        if not message:
            return
        if file is None:
            file = sys.stderr
        if file is sys.stdout or file is sys.stderr:
            if self._stream is not None:
                file = self._stream
        file.write(message)


def _build_parser(stderr=None) -> argparse.ArgumentParser:
    parser = _StreamArgumentParser(prog="compare_cli.py", stream=stderr, allow_abbrev=False)
    parser.add_argument("--run-dir", required=True,
                        help="the jev_assisted run directory holding the traces")
    parser.add_argument("--llm-usage", required=True,
                        help="the plain-LLM usage entry read out of CC Switch by hand; "
                             "its optional upgraded_llm_usage block carries the escalated "
                             "items' plain-LLM calls, tokens and cost")
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

    parser = _build_parser(err)
    if not arguments:
        parser.print_usage(err)
        return 2
    try:
        args = parse_known_options(parser, arguments, stderr=err)
    except SystemExit as exit_error:
        return 2 if exit_error.code else 0

    try:
        # Pre-flight the export destination. An unusable directory must be
        # reported as such, and nothing may touch the disk until every gate has
        # passed.
        export_dir = None
        if args.output_dir:
            if not args.plugin_root:
                raise ValueError("--plugin-root is required when exporting")
            export_dir = require_explicit_output_dir(args.output_dir, args.plugin_root)
        policy = load_policy(args.policy or default_policy_path())
        report = build_case_report(
            run_dir=Path(args.run_dir), llm_usage_path=Path(args.llm_usage),
            policy=policy,
        )
        if export_dir is not None:
            _export(export_dir, report)
    except (ContractError, JevClientError, OSError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False),
              file=err)
        return 1

    print(json.dumps(report, ensure_ascii=False, sort_keys=True), file=out)
    return 0


def _export(output_dir, report) -> Path:
    """Write the report through the session-export bundle helper.

    The bundle directory is created by the helper, and only after its own gate
    has passed, so a refused export leaves nothing behind. `exported_to` is
    filled in before rendering, because the copy on disk is the artifact of
    record and must carry the same value as the printed one.
    """

    bundle = export_session(
        SESSION_ID, [], ["comparison.json", "comparison.md"], [],
        output_dir, PLUGIN_ROOT,
    )
    report["exported_to"] = str(bundle)
    (bundle / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (bundle / "comparison.md").write_text(
        report_to_markdown(report)
        + f"\n导出位置：{bundle}\n生成时间：{now_iso()}\n",
        encoding="utf-8",
    )
    return bundle


if __name__ == "__main__":
    raise SystemExit(main())
