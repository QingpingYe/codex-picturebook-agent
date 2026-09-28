"""CLI for the text_quality_prefilter screening operation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The operation is composed from three skills: this CLI lives with the
# pre-output side but drives the shared decision runtime and the knowledge
# side's red-line catalog, so every script directory it reads through joins the
# import path before the sibling modules are imported.
SCRIPT_DIR = Path(__file__).resolve().parent
KNOWLEDGE_SCRIPTS = Path(__file__).resolve().parents[2] / "knowledge-loader" / "scripts"
RUNTIME_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "jev-decision-runtime" / "scripts"
)
for scripts_path in (KNOWLEDGE_SCRIPTS, RUNTIME_SCRIPTS, SCRIPT_DIR):
    if str(scripts_path) not in sys.path:
        sys.path.insert(0, str(scripts_path))

from decision_contract import (  # noqa: E402
    ContractError,
    default_policy_path,
    is_valid_run_id,
    load_policy,
    operation_policy,
)
from evidence_bundle import require_bundle_shape  # noqa: E402
from jev_client import JevClient, JevClientError, UrllibTransport  # noqa: E402
from jev_runner import (  # noqa: E402
    AmbiguousAttempt,
    LeaseHeld,
    NoPendingCall,
    RunnerConfig,
    parse_known_options,
    reject_credential_arguments,
    write_atomic,
)
from page_quality import parse_script_pages  # noqa: E402
from redline_catalog import catalog_from_bundle, rule_triples  # noqa: E402
from redline_proxy import scan_redlines  # noqa: E402
from screening import may_skip_llm_review  # noqa: E402
from screening_runner import OPERATION, run_screening  # noqa: E402

REPORT_SCHEMA = "pb-quality-prefilter-report-v1"
ESCALATION_SCHEMA = "pb-quality-escalation-package-v1"


class _StreamParser(argparse.ArgumentParser):
    """An `ArgumentParser` that reports through the streams `main` was handed.

    argparse writes usage and errors to the process streams. That would leave an
    injected-stderr test asserting on a buffer nothing ever writes to — and,
    worse, it would echo onto the terminal the very argument the credential
    refusal exists to keep off it.
    """

    def __init__(self, *args, stdout=None, stderr=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._stdout = stdout
        self._stderr = stderr

    def _print_message(self, message, file=None):
        if file is sys.stderr:
            file = self._stderr
        elif file is sys.stdout or file is None:
            file = self._stdout
        super()._print_message(message, file)

    def print_usage(self, file=None):
        super().print_usage(self._stderr if file is None else file)

    def error(self, message):
        self.print_usage()
        self._print_message(f"{self.prog}: error: {message}\n", sys.stderr)
        raise SystemExit(2)

    def exit(self, status=0, message=None):
        if message:
            self._print_message(message, sys.stdout if status == 0 else sys.stderr)
        raise SystemExit(status)


def _build_parser(stdout=None, stderr=None) -> argparse.ArgumentParser:
    parser = _StreamParser(prog="screening_cli.py", stdout=stdout, stderr=stderr)
    parser.add_argument(
        "--script", required=True, help="the storyboard draft markdown to screen"
    )
    parser.add_argument("--bundle", required=True, help="authority evidence bundle JSON")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--run-id", help="override the run directory name as the run id")
    parser.add_argument(
        "--age-band", default="", help="the target age band, e.g. 3-6"
    )
    parser.add_argument(
        "--window-width",
        type=int,
        default=1,
        help=(
            "1 screens each page alone; a larger value makes the window the "
            "anchor page plus that many following pages"
        ),
    )
    parser.add_argument(
        "--escalation-out", help="write the escalation package for the plain LLM here"
    )
    parser.add_argument("--report-out", help="write the full report here")
    parser.add_argument("--policy")
    return parser


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


def _read_script(path: str) -> tuple[str, tuple[dict, ...]]:
    """Read the draft's per-page table, refusing a file that carries none.

    "No page row" and "a page with nothing to review" are different answers. The
    first is a file this screen could not read as a storyboard, and reporting it
    as a finished screen of nothing would read exactly like "the draft passed".
    The second is normal — a wordless spread carries no text — and is reported
    as `item_count = 0` with its own warning instead.
    """

    markdown = Path(path).read_text(encoding="utf-8")
    pages = parse_script_pages(markdown)
    if not pages:
        raise ContractError(
            f"no per-page table rows found in {path}: the pre-screen reads the "
            "storyboard's page table, and a file that cannot be read as one is "
            "not a draft it may report as clear"
        )
    return markdown, pages


def _screenable_item_count(outcome) -> int:
    """How many page windows the screen produced a verdict for."""

    return len({decision.item_id.split("::")[0] for decision in outcome.decisions})


def _warnings(*, outcome, rules, calibration_status, item_count, age_band) -> list[str]:
    """The notes a reader needs so an empty or failed screen is not read as a pass."""

    notes: list[str] = []
    if not rules:
        notes.append(
            "红线词表为空：本次预筛没有覆盖任何红线。这是词表缺口，"
            "不是「草稿没有红线」。"
        )
    if item_count == 0:
        notes.append(
            "没有可筛页面窗口（所有页面都没有文字，或页面表没有可读文字列）："
            "空报告不是「通过」。"
        )
    if calibration_status != "calibrated":
        notes.append(
            f"operation 的 calibration_status 仍是 {calibration_status}："
            "screened_clear 只产生对比数据，不缩减普通 LLM 复核范围。"
        )
    if outcome.blocked_records:
        notes.append(
            "运行目录里有无法读作本次请求的记录（见 blocked_records）："
            "相关批次按 runtime_failure 处理，不会被重新发送，需人工处理后重跑。"
        )
    if not age_band:
        notes.append("未提供 --age-band：年龄理解风险维度缺少目标年龄上下文。")
    return notes


def _build_report(
    *,
    run_id,
    age_band,
    window_width,
    page_count,
    item_count,
    calibration_status,
    proxy_hit_count,
    outcome,
    warnings,
) -> dict:
    """The operator-facing report: verdicts, ratios, and what stopped the run."""

    skip_items = [
        decision.item_id
        for decision in outcome.decisions
        if may_skip_llm_review(decision, calibration_status)
    ]
    return {
        "schema_version": REPORT_SCHEMA,
        "operation": OPERATION,
        "run_id": run_id,
        "age_band": age_band,
        "window_width": window_width,
        "page_count": page_count,
        "item_count": item_count,
        "catalog_size": outcome.catalog_size,
        "catalog_gap": outcome.catalog_size == 0,
        "calibration_status": calibration_status,
        # The reduction is per item, so the report counts the items whose clear
        # verdict may actually skip the review rather than claiming the whole
        # review may be skipped. While the operation is experimental both are
        # empty however many dimensions cleared.
        "may_skip_llm_review_items": skip_items,
        "may_skip_llm_review_count": len(skip_items),
        "proxy_hit_count": proxy_hit_count,
        "summary": dict(outcome.summary),
        "routes": [dict(route) for route in outcome.routes],
        "results": [dict(result) for result in outcome.results],
        "blocked_records": [dict(record) for record in outcome.blocked_records],
        "proxy_conflicts": [dict(conflict) for conflict in outcome.proxy_conflicts],
        "escalation_package": [
            dict(entry) for entry in outcome.escalation_package
        ],
        "warnings": list(warnings),
    }


def _escalation_payload(report: dict, items) -> dict:
    """The escalation package handed to the plain LLM, with its own context."""

    return {
        "schema_version": ESCALATION_SCHEMA,
        "operation": report["operation"],
        "run_id": report["run_id"],
        "calibration_status": report["calibration_status"],
        # Deliberately no report-wide "the review may be skipped" flag here: the
        # package is what the plain LLM reviews, and the reduction spec §7.4
        # allows is per item, not per report.
        "may_skip_llm_review_count": report["may_skip_llm_review_count"],
        "catalog_gap": report["catalog_gap"],
        "summary": report["summary"],
        "warnings": report["warnings"],
        "items": [dict(entry) for entry in items],
    }


def main(argv=None, environ=None, transport_factory=None, stdout=None, stderr=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr

    refusal = reject_credential_arguments(arguments)
    if refusal is not None:
        print(refusal, file=err)
        return 2

    parser = _build_parser(stdout=out, stderr=err)
    if not arguments:
        parser.print_usage(err)
        return 2
    try:
        args = parse_known_options(parser, arguments, stderr=err)
    except SystemExit as exit_error:
        return 2 if exit_error.code else 0

    try:
        run_id = _resolve_run_id(args)
        if args.window_width < 1:
            raise ContractError(
                f"--window-width {args.window_width} must be at least 1"
            )
        markdown, pages = _read_script(args.script)
        bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
        require_bundle_shape(bundle)
        policy = load_policy(args.policy or default_policy_path())
        entry = operation_policy(policy, OPERATION)
        calibration_status = str(entry.get("calibration_status", "experimental"))
        # The catalog is the recall boundary, and the literal scan over the
        # draft is extra evidence for it: a hit adds a candidate the review has
        # to judge, it never narrows which red lines get asked about.
        rules = catalog_from_bundle(bundle)
        proxy_findings = scan_redlines(markdown, rule_triples(rules))
        config = RunnerConfig(run_dir=Path(args.run_dir), policy=policy)
        factory = transport_factory or UrllibTransport
        client = JevClient(factory(), environ=environ)
        outcome = run_screening(
            run_id=run_id,
            policy=policy,
            pages=pages,
            rules=rules,
            bundle=bundle,
            config=config,
            client=client,
            age_band=args.age_band,
            proxy_findings=proxy_findings,
            window_width=args.window_width,
        )
        item_count = _screenable_item_count(outcome)
        warnings = _warnings(
            outcome=outcome,
            rules=rules,
            calibration_status=calibration_status,
            item_count=item_count,
            age_band=args.age_band,
        )
        report = _build_report(
            run_id=run_id,
            age_band=args.age_band,
            window_width=args.window_width,
            page_count=len(pages),
            item_count=item_count,
            calibration_status=calibration_status,
            proxy_hit_count=len(proxy_findings),
            outcome=outcome,
            warnings=warnings,
        )
        # Both artifacts are written inside the guard, with the runtime's atomic
        # writer: an unwritable path must reach the documented JSON error
        # instead of losing the whole report to a traceback after the screening
        # calls have already been paid for.
        if args.escalation_out:
            write_atomic(
                args.escalation_out,
                _escalation_payload(report, outcome.escalation_package),
            )
        if args.report_out:
            write_atomic(args.report_out, report)
    except (ContractError, JevClientError, NoPendingCall, LeaseHeld, AmbiguousAttempt) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False), file=err)
        return 1
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False), file=err)
        return 1

    print(json.dumps(report, ensure_ascii=False, sort_keys=True), file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
