"""The sole subprocess boundary for Lark CLI access.

All calls are JSON-oriented, injected runners make the boundary fully unit-testable,
and CLI output is never surfaced without redacting credential-shaped values.
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping


class LarkCliError(RuntimeError):
    pass


class CliUnavailable(LarkCliError):
    pass


class AuthenticationError(LarkCliError):
    pass


class PermissionDenied(LarkCliError):
    pass


class ResourceNotFound(LarkCliError):
    pass


class RateLimited(LarkCliError):
    pass


class TransientFailure(LarkCliError):
    pass


class RevisionConflict(LarkCliError):
    pass


_TOKEN_VALUE = re.compile(
    r"(?i)(\b(?:access[_-]?token|refresh[_-]?token|token|authorization|bearer)\b[\"']?\s*(?:=|:)+\s*[\"']?)([^\s,}\]\"']+)"
)
_BEARER_CREDENTIAL = re.compile(r"(?i)(\bauthorization\b\s*:\s*bearer\s+|\bbearer\s+)(\S+)")
_LONG_SECRET = re.compile(r"(?<![\w-])[A-Za-z0-9_=-]{24,}(?![\w-])")


class LarkCli:
    """Typed command adapter.  Direct ``subprocess.run`` is intentionally isolated here."""

    def __init__(
        self,
        preferred_binary: Path | str,
        identity: str = "user",
        runner: Callable[..., Any] = subprocess.run,
    ) -> None:
        if identity != "user":
            raise ValueError("identity must be 'user'")
        self.binary = Path(preferred_binary)
        self.identity = identity
        self.runner = runner

    def preflight(self, target_root_token: str) -> dict[str, Any]:
        """Verify user authentication and root readability without making mutations."""
        auth = self._json("auth", "status", "--json", "--verify")
        node = self.get_node(target_root_token)
        return {"auth": auth, "root": node}

    def get_node(self, node_token: str) -> dict[str, Any]:
        return self._json(
            "wiki", "+node-get", "--as", self.identity, "--node-token", node_token, "--format", "json"
        )

    def list_nodes(self, space_id: str, parent_node_token: str | None = None,
                   page_limit: int = 10) -> list[dict[str, Any]]:
        args = [
            "wiki", "+node-list", "--as", self.identity,
            "--space-id", space_id, "--page-all", "--page-limit", str(page_limit), "--format", "json",
        ]
        if parent_node_token:
            args.extend(["--parent-node-token", parent_node_token])
        return self._items(self._json(*args))

    def create_doc(self, parent_node_token: str, title: str, content: str = "") -> dict[str, Any]:
        path = self._temp_content_file(content)
        try:
            return self._json(
                "docs", "+create", "--as", self.identity, "--parent-token", parent_node_token,
                "--title", title, "--doc-format", "markdown", "--content", f"@{path}",
            )
        finally:
            Path(path).unlink(missing_ok=True)

    def fetch_doc(self, doc_token: str) -> dict[str, Any]:
        return self._json(
            "docs", "+fetch", "--as", self.identity,
            "--doc", doc_token, "--doc-format", "markdown",
        )

    def fetch_doc_revision(self, doc_token: str, revision_id: int) -> dict[str, Any]:
        return self._json(
            "docs", "+get", "--as", self.identity, "--doc", doc_token,
            "--revision-id", str(revision_id), "--format", "json",
        )

    def update_doc(self, doc_token: str, revision_id: int, content: str) -> dict[str, Any]:
        path = self._temp_content_file(content)
        try:
            result = self._json(
                "docs", "+update", "--as", self.identity, "--doc", doc_token,
                "--command", "overwrite", "--doc-format", "markdown",
                "--revision-id", str(revision_id), "--content", f"@{path}",
            )
        finally:
            Path(path).unlink(missing_ok=True)
        try:
            int(result["data"]["document"]["revision_id"])
            return result
        except (KeyError, TypeError, ValueError) as error:
            raise LarkCliError("update response did not include document revision_id") from error

    def _json(self, *arguments: str) -> dict[str, Any]:
        command = [str(self.binary), *arguments]
        try:
            completed = self.runner(
                command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
            )
        except FileNotFoundError as error:
            raise CliUnavailable(f"lark-cli executable unavailable: {self.binary}") from error
        except OSError as error:
            raise TransientFailure(f"could not execute lark-cli: {self._redact(str(error))}") from error

        stdout = self._text(getattr(completed, "stdout", ""))
        stderr = self._text(getattr(completed, "stderr", ""))
        payload = self._final_json(stdout)
        if getattr(completed, "returncode", 0) != 0 or self._is_error_payload(payload):
            self._raise_command_error(payload, stdout, stderr)
        if payload is None:
            raise LarkCliError("lark-cli returned no JSON payload")
        return payload

    def _raw(self, *arguments: str) -> str:
        command = [str(self.binary), *arguments]
        try:
            completed = self.runner(
                command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        except FileNotFoundError as error:
            raise CliUnavailable(f"lark-cli executable unavailable: {self.binary}") from error
        except OSError as error:
            raise TransientFailure(f"could not execute lark-cli: {self._redact(str(error))}") from error
        return self._text(getattr(completed, "stdout", "")) + self._text(getattr(completed, "stderr", ""))

    @staticmethod
    def _temp_content_file(content: str) -> Path:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False) as file:
            file.write(content)
            return Path(file.name)

    @staticmethod
    def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data", payload)
        items = data.get("items", data.get("nodes", [])) if isinstance(data, Mapping) else []
        if not isinstance(items, list):
            raise LarkCliError("unexpected node-list response")
        return items

    def verify_supported_version(self) -> dict[str, str]:
        text = self._raw("--version").strip()
        match = re.search(r"lark-cli.*?([0-9]+\.[0-9]+\.[0-9]+)", text, flags=re.IGNORECASE)
        if match is None:
            raise CliUnavailable("未识别 lark-cli 版本")
        version = match.group(1)
        if version not in {"1.0.95", "1.0.96"}:
            raise CliUnavailable(f"暂不支持的 lark-cli 版本：{version}")
        return {"version": version}

    @staticmethod
    def _text(value: Any) -> str:
        return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value or "")

    @staticmethod
    def _final_json(stdout: str) -> dict[str, Any] | None:
        stripped = stdout.strip()
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        decoder = json.JSONDecoder()
        candidates: list[dict[str, Any]] = []
        for start, char in enumerate(stdout):
            if char != "{":
                continue
            candidate = stdout[start:].lstrip()
            if not candidate.startswith("{"):
                continue
            try:
                parsed, _ = decoder.raw_decode(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                if "data" in parsed or "code" in parsed:
                    return parsed
                candidates.append(parsed)
        return candidates[0] if candidates else None

    @staticmethod
    def _is_error_payload(payload: dict[str, Any] | None) -> bool:
        return bool(payload and (payload.get("code") not in (None, 0, "0") or payload.get("error")))

    def _raise_command_error(self, payload: dict[str, Any] | None, stdout: str, stderr: str) -> None:
        serialized = json.dumps(payload, ensure_ascii=False) if payload is not None else ""
        message = self._redact(" ".join(part for part in (serialized, stderr, stdout) if part).strip())
        lowered = message.lower()
        if "revision" in lowered and ("conflict" in lowered or "changed" in lowered):
            error_type = RevisionConflict
        elif any(term in lowered for term in ("auth", "unauth", "login", "access_token", "credential", "401")):
            error_type = AuthenticationError
        elif any(term in lowered for term in ("permission", "forbidden", "access denied", " 403")):
            error_type = PermissionDenied
        elif any(term in lowered for term in ("not found", " 404", "not_exist")):
            error_type = ResourceNotFound
        elif any(term in lowered for term in ("rate limit", "too many", " 429", "quota")):
            error_type = RateLimited
        else:
            error_type = TransientFailure
        raise error_type(message or "lark-cli command failed")

    @staticmethod
    def _redact(value: str) -> str:
        value = _BEARER_CREDENTIAL.sub(r"\1[REDACTED]", value)
        value = _TOKEN_VALUE.sub(r"\1[REDACTED]", value)
        return _LONG_SECRET.sub("[REDACTED]", value)
