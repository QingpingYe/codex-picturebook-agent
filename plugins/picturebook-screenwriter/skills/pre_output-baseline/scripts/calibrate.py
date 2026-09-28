"""Threshold calibration over human-labelled Chinese samples.

The tool measures; it never writes policy. `decision-policies.json` stays the
only authority for routing bands, and everything printed here is a
recommendation a human confirms before the shipped `clear_at_or_below` value (or
an operation's `calibration_status`) changes. That is why the sweep grid lives
in this file as a list of *candidate* boundaries instead of in the policy.

The boundary under test is the policy's own `clear_at_or_below`: an item is
cleared when its probability is at or below it, so a higher boundary clears more
and escalates less. A measurement that cannot be read is never counted as clear,
because a pre-screen may not clear what it could not read.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

_RUNTIME_SCRIPTS = Path(__file__).resolve().parents[2] / "jev-decision-runtime" / "scripts"
if str(_RUNTIME_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_SCRIPTS))

# The operation vocabulary has exactly one authority. Re-declaring it here would
# let a sample file name an operation the runtime cannot screen.
# The bands are read from the shipped policy for the same reason: the ceiling
# below which a clear boundary has to stay is a property of the operation, not
# of this tool's grid.
from decision_contract import (  # noqa: E402
    OPERATIONS,
    default_policy_path,
    load_policy,
    operation_policy,
)

LABELS = ("clear", "issue", "borderline")

# Below this many labelled samples a threshold recommendation is noise, so the
# tool refuses rather than producing a number someone might paste into policy.
MIN_SAMPLES = 4

# Candidate boundaries, deliberately coarse and deliberately not the shipped
# ones: the point is to show which false-negative budgets are reachable, not to
# fit the sample set. Nothing here is sent to the model or written to a policy.
DEFAULT_SWEEP = ("0.10", "0.15", "0.20", "0.25", "0.30", "0.35", "0.40",
                 "0.50", "0.60", "0.70", "0.80")

# A sample row carries the full seven-column annotation; the prose and the
# two-column label legend have to stay unreadable as samples.
TABLE_COLUMNS = 7
SAMPLE_HEADER = "sample_id"


@dataclass(frozen=True)
class CalibrationSample:
    """One human-labelled screening item."""

    sample_id: str
    operation: str
    item_id: str
    dimension: str
    label: str
    evidence: str
    text: str


@dataclass(frozen=True)
class SampleOutcome:
    """One measured probability, joined to the sample it was measured on."""

    sample: CalibrationSample
    probability: float | None
    failed: bool


def _split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_samples(markdown: str) -> tuple[CalibrationSample, ...]:
    """Read the labelled sample tables out of a Markdown reference file.

    Only rows carrying the full column set are read, so the prose, the label
    legend and the fenced command examples in the same file are ignored. A row
    that does carry the columns is validated: an unknown label or an operation
    the runtime cannot screen is an annotation error, not a row to skip.
    """

    samples: list[CalibrationSample] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = _split_row(stripped)
        if len(cells) != TABLE_COLUMNS or cells[0] == SAMPLE_HEADER:
            continue
        if set(cells[0]) <= set("-: "):
            continue
        sample_id, operation, item_id, dimension, label, evidence, text = cells
        if not sample_id:
            raise ValueError("a sample row is missing its sample_id")
        if label not in LABELS:
            raise ValueError(f"unknown label for {sample_id}: {label!r}")
        if operation not in OPERATIONS:
            raise ValueError(f"unknown operation for {sample_id}: {operation!r}")
        samples.append(CalibrationSample(
            sample_id, operation, item_id, dimension, label, evidence, text
        ))
    return tuple(samples)


def load_samples(path: Any) -> tuple[CalibrationSample, ...]:
    """Read the labelled sample file.

    A file with no readable sample row is a gap in the calibration set, not a
    baseline to measure against.
    """

    samples = parse_samples(Path(path).read_text(encoding="utf-8"))
    if not samples:
        raise ValueError(f"no labelled samples found in {path}")
    return samples


def load_outcomes(path: Any) -> tuple[SampleOutcome, ...]:
    """Read measured probabilities, keyed by sample id.

    Only the measurement is stored on disk. The label always comes from the
    sample file, so a measurement file can never carry its own answer.
    """

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("outcomes file must contain a JSON array")
    outcomes: list[SampleOutcome] = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise ValueError("every outcome must be a JSON object")
        sample_id = entry.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id:
            raise ValueError("every outcome needs a non-empty sample_id")
        failed = entry.get("failed", False)
        if not isinstance(failed, bool):
            raise ValueError(f"failed must be true or false for {sample_id}")
        probability = entry.get("probability")
        if isinstance(probability, bool):
            # `float(True)` would read as a certain red line instead of the
            # unreadable measurement it is.
            raise ValueError(f"{sample_id} has an unreadable probability")
        if probability is not None and _measurement(probability) is None:
            raise ValueError(f"{sample_id} has an unreadable probability")
        if not failed and _measurement(probability) is None:
            raise ValueError(
                f"{sample_id} has no readable probability and is not marked failed"
            )
        outcomes.append(SampleOutcome(
            CalibrationSample(sample_id, "", "", "", "", "", ""),
            None if probability is None else float(probability),
            failed,
        ))
    return tuple(outcomes)


def _measurement(value: Any) -> Decimal | None:
    """Return a measured probability as an exact Decimal, or None if unreadable.

    Decimal is the comparison the runtime uses: a boundary landing exactly on a
    band edge must not flip on binary rounding. A value that is not a finite
    number inside `[0, 1]` is not a measurement, and an absent measurement is
    never on the clear side.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        # `float(True)` is 1.0, and a boolean is never a measured probability.
        # Reading one as a certain red line would put a made-up number on the
        # risk side of every boundary.
        return None
    try:
        number = float(value)
        exact = Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        return None
    return exact


def _join(
    outcomes: Sequence[SampleOutcome], samples_path: Any
) -> tuple[SampleOutcome, ...]:
    """Attach the real sample records to the measured probabilities."""

    by_id = {sample.sample_id: sample for sample in load_samples(samples_path)}
    joined = []
    for entry in outcomes:
        sample = by_id.get(entry.sample.sample_id)
        if sample is None:
            raise ValueError(
                f"outcome refers to an unknown sample: {entry.sample.sample_id}"
            )
        joined.append(SampleOutcome(sample, entry.probability, entry.failed))
    return tuple(joined)


def sweep(
    outcomes: Sequence[SampleOutcome], thresholds: Sequence[str]
) -> tuple[dict, ...]:
    """Build one confusion matrix per candidate `clear_at_or_below` boundary.

    The rule under test is the shipped one: clear at or below the boundary. A
    failed or unreadable measurement joins the escalated side on every
    threshold, so it can never be counted as clear.
    """

    entries = tuple(outcomes)
    total = len(entries)
    for entry in entries:
        if entry.sample.label not in LABELS:
            # A measurement carries no label of its own, so a sweep over raw
            # outcomes would count every sample as neither a false negative nor
            # a false positive and read as if it were safe. Refuse instead.
            raise ValueError(
                "every outcome needs a labelled sample; "
                f"{entry.sample.sample_id!r} has {entry.sample.label!r}"
            )
    rows = []
    for raw in thresholds:
        threshold = Decimal(str(raw))
        cleared = escalated = failed = 0
        false_negatives = false_positives = borderline_cleared = 0
        for entry in entries:
            probability = _measurement(entry.probability)
            if entry.failed or probability is None:
                failed += 1
                continue
            if probability <= threshold:
                cleared += 1
                if entry.sample.label == "issue":
                    false_negatives += 1
                if entry.sample.label == "borderline":
                    borderline_cleared += 1
            else:
                escalated += 1
                if entry.sample.label == "clear":
                    false_positives += 1
        rows.append({
            "threshold": str(raw),
            "screened_clear": cleared,
            "escalated": escalated,
            "failed": failed,
            "false_negatives": false_negatives,
            "false_positives": false_positives,
            "borderline_cleared": borderline_cleared,
            "escalation_rate": ((escalated + failed) / total) if total else 0.0,
            "false_negative_rate": (false_negatives / total) if total else 0.0,
        })
    return tuple(rows)


def recommend(
    outcomes: Sequence[SampleOutcome],
    *,
    max_false_negative_rate: float = 0.0,
    thresholds: Sequence[str] = DEFAULT_SWEEP,
    ceiling: str | None = None,
) -> dict:
    """Pick the loosest boundary that still honours the false-negative budget.

    Loosest means clearing the most items, which is what makes the screening
    worth running; the false-negative budget is the hard constraint, so the
    default of zero refuses any boundary that would clear an item a human
    labelled `issue` — the same place a hard constraint would be lost.

    `ceiling` is the operation's fixed `risk_at_or_above`, and a boundary must
    stay strictly below it. On the boundary itself every answer bands `clear`,
    which erases the grey band: the grey escalation rule becomes dead code and
    the content-removing `all_of` rules fire more often. A recommendation that
    could not be pasted into the policy is not a recommendation.

    Every caller has to pass `ceiling`; the CLI reads it from the operation's
    policy with `operation_ceiling`. The `None` default only exists so a test
    can sweep a grid without a policy in hand, and a new caller that forgets it
    gets the unguarded grid.
    """

    entries = tuple(outcomes)
    if len(entries) < MIN_SAMPLES:
        raise ValueError(
            f"at least {MIN_SAMPLES} labelled samples are required to recommend "
            "a threshold"
        )
    limit = None if ceiling is None else Decimal(str(ceiling))
    candidates = []
    excluded = []
    for raw in thresholds:
        if limit is not None and Decimal(str(raw)) >= limit:
            excluded.append(str(raw))
            continue
        candidates.append(raw)
    if not candidates:
        return {
            "threshold": None,
            "reason": "no_candidate_stays_below_the_risk_band",
            "max_false_negative_rate": max_false_negative_rate,
            "samples": len(entries),
            "ceiling": None if ceiling is None else str(ceiling),
            "excluded_thresholds": excluded,
        }
    rows = sweep(entries, candidates)
    acceptable = [
        row for row in rows if row["false_negative_rate"] <= max_false_negative_rate
    ]
    if not acceptable:
        return {
            "threshold": None,
            "reason": "no_threshold_meets_the_false_negative_budget",
            "max_false_negative_rate": max_false_negative_rate,
            "samples": len(entries),
            "ceiling": None if ceiling is None else str(ceiling),
            "excluded_thresholds": excluded,
        }
    best = max(
        acceptable, key=lambda row: (row["screened_clear"], float(row["threshold"]))
    )
    recommendation = {
        "threshold": best["threshold"],
        "max_false_negative_rate": max_false_negative_rate,
        "samples": len(entries),
        "ceiling": None if ceiling is None else str(ceiling),
        "excluded_thresholds": excluded,
        "screened_clear": best["screened_clear"],
        "escalated": best["escalated"],
        "failed": best["failed"],
        "false_negatives": best["false_negatives"],
        "false_positives": best["false_positives"],
        "borderline_cleared": best["borderline_cleared"],
        "escalation_rate": best["escalation_rate"],
        "false_negative_rate": best["false_negative_rate"],
    }
    if excluded:
        recommendation["warning"] = (
            "candidate thresholds at or above the operation's risk_at_or_above "
            f"were excluded because they would erase the grey band: {excluded}"
        )
    return recommendation


def operation_ceiling(operation: str) -> str | None:
    """The operation's fixed `risk_at_or_above`, the exclusive clear ceiling."""

    entry = operation_policy(load_policy(default_policy_path()), operation)
    bands = (entry.get("routing") or {}).get("bands") or {}
    ceiling = bands.get("risk_at_or_above")
    return None if ceiling is None else str(ceiling)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="calibrate.py")
    parser.add_argument("--samples", required=True)
    parser.add_argument("--outcomes", required=True)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--max-false-negative-rate", type=float, default=0.0)
    parser.add_argument("--threshold", action="append", default=[])
    return parser


def main(argv=None, stdout=None, stderr=None) -> int:
    """Report the sweep and the recommendation for one operation as JSON."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    parser = _build_parser()
    if not arguments:
        parser.print_usage(err)
        return 2
    try:
        args = parser.parse_args(arguments)
    except SystemExit as exit_error:
        return 2 if exit_error.code else 0

    try:
        # The operation vocabulary and its bands have one authority each, so an
        # unsupported operation fails here with the contract's own message
        # rather than as an empty sample set further down.
        ceiling = operation_ceiling(args.operation)
        joined = _join(load_outcomes(args.outcomes), args.samples)
        scoped = tuple(
            entry for entry in joined if entry.sample.operation == args.operation
        )
        if not scoped:
            raise ValueError(f"no measured outcomes for operation {args.operation!r}")
        thresholds = tuple(args.threshold) or DEFAULT_SWEEP
        payload = {
            "operation": args.operation,
            "samples": len(scoped),
            "ignored_outcomes": len(joined) - len(scoped),
            # Every sweep row above this ceiling is a measurement, not a
            # boundary anyone may paste into the policy.
            "clear_ceiling": ceiling,
            "sweep": list(sweep(scoped, thresholds)),
            "recommendation": recommend(
                scoped,
                max_false_negative_rate=args.max_false_negative_rate,
                thresholds=thresholds,
                ceiling=ceiling,
            ),
        }
    except (OSError, ValueError, ArithmeticError) as error:
        print(
            json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False),
            file=err,
        )
        return 1

    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
