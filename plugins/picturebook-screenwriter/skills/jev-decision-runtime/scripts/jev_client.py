"""Minimal standard-library client for the TypeSafe Jev SystemOne API."""

from __future__ import annotations

import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
ALLOWED_HOST = "api.typesafe.ai"
ENDPOINT_PATH = "/v1/systemone"
API_KEY_ENV = "TYPESAFE_API_KEY"

DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_TOTAL_TIMEOUT = 30.0

MAX_ATTEMPTS = 3
BACKOFF_INITIAL_SECONDS = 0.5
BACKOFF_MAX_SECONDS = 5.0
_RETRYABLE_STATUSES = (429, 529)


class JevClientError(RuntimeError):
    """Base class for client-side failures."""


class JevEndpointError(JevClientError):
    """The endpoint is not the allow-listed production endpoint."""


class JevTransportFailure(JevClientError):
    """The request provably never completed; reporting a failure is safe."""


class JevTransportOutcomeUnknown(JevClientError):
    """The request may have reached the service; the outcome cannot be known."""


@dataclass(frozen=True)
class TransportResponse:
    status_code: int
    body: str
    headers: Mapping[str, str]


class Transport(Protocol):
    def send(
        self, url: str, headers: Mapping[str, str], body: bytes, timeout: float
    ) -> TransportResponse: ...


@dataclass(frozen=True)
class CallOutcome:
    status: str
    status_code: int | None
    payload: Mapping[str, Any] | None
    error_class: str | None
    attempts: int


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    """Read retry-after-ms or retry-after as seconds; HTTP-date form is ignored."""

    lowered = {str(key).lower(): value for key, value in (headers or {}).items()}
    milliseconds = lowered.get("retry-after-ms")
    if milliseconds is not None:
        try:
            return float(milliseconds) / 1000.0
        except (TypeError, ValueError):
            return None
    seconds = lowered.get("retry-after")
    if seconds is None:
        return None
    try:
        return float(seconds)
    except (TypeError, ValueError):
        return None


def backoff_seconds(attempt: int, retry_after: float | None) -> float:
    if retry_after is not None and retry_after >= 0:
        return min(retry_after, BACKOFF_MAX_SECONDS)
    return min(BACKOFF_INITIAL_SECONDS * (2 ** (attempt - 1)), BACKOFF_MAX_SECONDS)


def read_api_key(environ: Mapping[str, str] | None = None) -> str | None:
    """Read the Jev key from the environment; empty or blank counts as missing."""

    source = os.environ if environ is None else environ
    raw = source.get(API_KEY_ENV)
    if not isinstance(raw, str):
        return None
    return raw.strip() or None


def assert_production_endpoint(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise JevEndpointError(f"endpoint must use https, got {parts.scheme!r}")
    if parts.hostname != ALLOWED_HOST:
        raise JevEndpointError(f"endpoint host is not allow-listed: {parts.hostname!r}")
    if parts.path != ENDPOINT_PATH:
        raise JevEndpointError(f"endpoint path is not allow-listed: {parts.path!r}")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect so credentials never travel to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibTransport:
    """Production transport, pinned to the allow-listed endpoint."""

    def __init__(
        self,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        total_timeout: float = DEFAULT_TOTAL_TIMEOUT,
    ) -> None:
        self.connect_timeout = connect_timeout
        self.total_timeout = total_timeout

    def _open(self, request: urllib.request.Request, timeout: float):
        opener = urllib.request.build_opener(_NoRedirectHandler())
        return opener.open(request, timeout=timeout)

    def send(
        self, url: str, headers: Mapping[str, str], body: bytes, timeout: float
    ) -> TransportResponse:
        assert_production_endpoint(url)
        request = urllib.request.Request(
            url, data=body, headers=dict(headers), method="POST"
        )
        try:
            with self._open(request, timeout) as response:
                return TransportResponse(
                    response.status, response.read().decode("utf-8"), dict(response.headers)
                )
        except urllib.error.HTTPError as error:
            return TransportResponse(
                error.code, error.read().decode("utf-8", "replace"), dict(error.headers)
            )
        except (socket.timeout, TimeoutError) as error:
            # A timeout can fire during connect or while reading the response.
            # We cannot tell which, and a duplicate billed call is worse than a
            # spurious "unknown", so timeouts always report the ambiguous case.
            raise JevTransportOutcomeUnknown(str(error)) from error
        except urllib.error.URLError as error:
            if isinstance(error.reason, (socket.timeout, TimeoutError)):
                raise JevTransportOutcomeUnknown(str(error.reason)) from error
            raise JevTransportFailure(str(error)) from error
        except (ssl.SSLError, ConnectionError, OSError) as error:
            raise JevTransportFailure(str(error)) from error


class FakeTransport:
    """Scripted transport for tests. Never reads the environment or opens a socket."""

    def __init__(self, responses=None, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._responses = list(responses or [])
        self._error = error

    def send(
        self, url: str, headers: Mapping[str, str], body: bytes, timeout: float
    ) -> TransportResponse:
        self.calls.append(
            {"url": url, "headers": dict(headers), "body": body, "timeout": timeout}
        )
        if self._error is not None:
            raise self._error
        if not self._responses:
            raise AssertionError("FakeTransport has no scripted response left")
        return self._responses.pop(0)


class JevClient:
    """Dispatch a decision request; holds the credential gate."""

    def __init__(
        self,
        transport: Transport,
        environ: Mapping[str, str] | None = None,
        sleep=time.sleep,
        total_timeout: float = DEFAULT_TOTAL_TIMEOUT,
    ) -> None:
        self._transport = transport
        self._environ = environ
        self._sleep = sleep
        self._total_timeout = total_timeout

    def call(self, request: Mapping[str, Any]) -> CallOutcome:
        key = read_api_key(self._environ)
        if key is None:
            return CallOutcome("waiting_for_jev_key", None, None, None, 0)
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = json.dumps(request, ensure_ascii=False).encode("utf-8")
        return self._attempt(request, headers, body)

    def _attempt(self, request, headers, body) -> CallOutcome:
        attempts = 0
        while attempts < MAX_ATTEMPTS:
            attempts += 1
            try:
                response = self._transport.send(
                    ENDPOINT, headers, body, self._total_timeout
                )
            except JevTransportOutcomeUnknown as error:
                # Ambiguous: retrying could duplicate a billed call.
                return CallOutcome("outcome_unknown", None, None, type(error).__name__, attempts)
            except JevTransportFailure as error:
                if attempts >= MAX_ATTEMPTS:
                    return CallOutcome("failed", None, None, type(error).__name__, attempts)
                self._sleep(backoff_seconds(attempts, None))
                continue
            if 300 <= response.status_code < 400:
                raise JevEndpointError(
                    f"redirect responses are not accepted: {response.status_code}"
                )
            outcome = self._classify(response, attempts)
            if outcome.status == "retry":
                if attempts >= MAX_ATTEMPTS:
                    return CallOutcome(
                        "failed", response.status_code, None,
                        f"http_{response.status_code}", attempts,
                    )
                self._sleep(backoff_seconds(attempts, outcome.payload))
                continue
            return outcome
        return CallOutcome("failed", None, None, "attempts_exhausted", attempts)

    def _classify(self, response: TransportResponse, attempts: int) -> CallOutcome:
        status = response.status_code
        if status == 200:
            try:
                payload = json.loads(response.body)
            except ValueError:
                return CallOutcome("failed", status, None, "invalid_json", attempts)
            return CallOutcome("succeeded", status, payload, None, attempts)
        if status == 401:
            return CallOutcome("waiting_for_jev_key", status, None, "unauthorized", attempts)
        if status == 403:
            return CallOutcome("waiting_for_jev_access", status, None, "forbidden", attempts)
        if status == 422:
            return CallOutcome("failed", status, None, "unprocessable_entity", attempts)
        if status in _RETRYABLE_STATUSES:
            return CallOutcome(
                "retry", status, parse_retry_after(response.headers), None, attempts
            )
        return CallOutcome("failed", status, None, f"http_{status}", attempts)
