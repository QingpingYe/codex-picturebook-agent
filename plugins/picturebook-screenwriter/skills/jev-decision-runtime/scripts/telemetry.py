"""Timing, usage, cost, and benchmark-case identity for Jev decisions."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from decision_contract import TRACE_SCHEMA

# Prices are billed per input token; output tokens are free. Keep the
# arithmetic in Decimal so a report never disagrees with the invoice.
_COST_QUANTUM = Decimal("1E-12")
_TOKENS_PER_MILLION = Decimal(1_000_000)
_CN_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(_CN_TZ).isoformat(timespec="milliseconds")


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def request_fingerprint(request: Mapping[str, Any]) -> str:
    """Stable identity of a request; benchmark_case_id is derived, not identity."""

    stable = {key: value for key, value in request.items() if key != "benchmark_case_id"}
    return "sha256:" + sha256_hex(canonical_json(stable))


def benchmark_case_id(normalized_input: Any, policy_version: str, rule_version: str) -> str:
    payload = {
        "input": normalized_input,
        "policy_version": policy_version,
        "rule_version": rule_version,
    }
    return "sha256:" + sha256_hex(canonical_json(payload))


def _is_token_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def estimate_cost_usd(input_tokens: int, price_usd_per_million_input_tokens: str) -> str:
    price = Decimal(price_usd_per_million_input_tokens)
    cost = (Decimal(int(input_tokens)) * price / _TOKENS_PER_MILLION).quantize(
        _COST_QUANTUM, rounding=ROUND_HALF_UP
    )
    # `str()` switches to scientific notation when the adjusted exponent drops
    # below -6 ("4.2000E-8", "0E-12"), which is not the plain fixed-point
    # decimal string the trace contract promises. Format explicitly.
    return format(cost, "f")


def estimate_usage(
    usage: Any, price_usd_per_million_input_tokens: str
) -> tuple[int | None, int | None, str | None]:
    """Return (input_tokens, output_tokens, estimated_cost_usd); never invent tokens."""

    if not isinstance(usage, Mapping):
        return None, None, None
    input_tokens = usage.get("input_tokens")
    if not _is_token_count(input_tokens):
        return None, None, None
    output_tokens = usage.get("output_tokens")
    if not _is_token_count(output_tokens):
        output_tokens = None
    cost = estimate_cost_usd(input_tokens, price_usd_per_million_input_tokens)
    return input_tokens, output_tokens, cost


class Stopwatch:
    """Monotonic elapsed-time measurement with an injectable clock."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._started: float | None = None
        self._elapsed_ms: int | None = None

    def start(self) -> None:
        self._started = self._clock()
        self._elapsed_ms = None

    def stop(self) -> None:
        if self._started is None:
            raise RuntimeError("stopwatch was never started")
        self._elapsed_ms = int(round((self._clock() - self._started) * 1000))

    @property
    def elapsed_ms(self) -> int:
        if self._elapsed_ms is None:
            raise RuntimeError("stopwatch has not been stopped")
        return self._elapsed_ms


@dataclass(frozen=True)
class DecisionTrace:
    run_id: str
    benchmark_case_id: str
    execution_mode: str
    operation: str
    started_at: str
    finished_at: str
    elapsed_ms: int
    input_sha256: str
    item_count: int
    question_count: int
    screened_clear_count: int
    escalated_count: int
    request_count: int
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: str | None
    price_usd_per_million_input_tokens: str
    price_snapshot_date: str
    resolved_model: str | None
    status: str
    fallback_used: bool
    error_class: str | None

    def to_dict(self) -> dict:
        payload = {"schema_version": TRACE_SCHEMA}
        payload.update(self.__dict__)
        return payload
