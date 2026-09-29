"""Normalize Markdown that Feishu returns after a docx page round-trip."""

import re
from typing import Literal


class MarkdownNormalizeError(ValueError):
    pass


Kind = Literal["control", "page"]


def normalize_remote_markdown(content: str, kind: Kind) -> str:
    if not isinstance(content, str) or not content.strip():
        raise MarkdownNormalizeError("远程文档内容不能为空")

    lines = content.splitlines()
    start = 0
    if lines and lines[0].startswith("<title>"):
        start = 1
    while start < len(lines) and not lines[start].strip():
        start += 1
    normalized = "\n".join(lines[start:])

    if kind == "control":
        normalized = re.sub(r"(?m)^(#[^\n]+)\n+(```)", r"\1\n\2", normalized, count=1)
    elif kind != "page":
        raise MarkdownNormalizeError(f"未知的远程文档类型：{kind}")

    return normalized.rstrip() + "\n"


def mark_insensitive(body: str) -> str:
    """Normalize presentation-only Markdown syntax for body comparison."""
    if not isinstance(body, str):
        raise TypeError("body must be text")
    protected: list[str] = []

    def protect(value: str) -> str:
        protected.append(value)
        return f"\x00{len(protected)-1}\x00"

    work = body.replace("\r\n", "\n").replace("\r", "\n")
    # Keep code literal, including all Markdown delimiters within it. CommonMark
    # permits a closing fence longer than its opening fence.
    lines = work.split("\n")
    fenced: list[str] = []
    i = 0
    opening = re.compile(r"^ {0,3}(`{3,}|~{3,})")
    while i < len(lines):
        match = opening.match(lines[i])
        if not match:
            fenced.append(lines[i])
            i += 1
            continue
        delimiter = match.group(1)
        marker, width = delimiter[0], len(delimiter)
        close = re.compile(rf"^ {{0,3}}{re.escape(marker)}{{{width},}}[ \t]*$")
        block = [lines[i]]
        i += 1
        while i < len(lines):
            block.append(lines[i])
            if close.match(lines[i]):
                i += 1
                break
            i += 1
        fenced.append(protect("\n".join(block)))
    work = "\n".join(fenced)
    work = re.sub(r"(`+)([^`\n]*?)\1", lambda m: protect(m.group(0)), work)

    # The one link form considered purely presentational is [URL](URL).
    work = re.sub(r"\[(https?://[^\]\s]+)\]\(\1\)", r"\1", work)
    # Protect ordinary link destinations and remaining URLs from emphasis and unescaping.
    work = re.sub(r"\]\([^)]*\)", lambda m: protect(m.group(0)), work)
    def protect_url(match: re.Match[str]) -> str:
        url = match.group(0)
        # Sentence punctuation can follow closing emphasis without whitespace.
        # Leave both outside the protected span so the normal delimiter checks
        # can remove paired marks; punctuation and unpaired marks remain literal.
        end = len(url.rstrip(".,!?;:\u3002\uff0c\uff01\uff1f\uff1b\uff1a\u3001\u2026"))
        core = url[:end].rstrip("*_")
        suffix = url[len(core):] if len(core) < end else ""
        return protect(url[:-len(suffix)] if suffix else url) + suffix
    work = re.sub(r"https?://[^\s)]+", protect_url, work)

    lines = []
    for line in work.split("\n"):
        line = re.sub(r"^ {0,3}#{1,6}[ \t]+", "", line)
        # Nested blockquote markers require the Markdown quote delimiter plus whitespace.
        line = re.sub(r"^(?: {0,3}>[ \t]+)+", "", line)
        line = re.sub(r"[ \t]+$", "", line)
        # Only table rows (leading pipe and at least two cells) can have a trailing empty cell.
        if re.match(r"^\s*\|.*\|\s*$", line) and line.count("|") >= 2:
            line = re.sub(r"\|[ \t]*$", "", line).rstrip(" \t")
        lines.append(line)
    work = "\n".join(lines)

    for marker in ("**", "__", "*", "_"):
        pattern = re.compile(rf"{re.escape(marker)}([^\n]+?){re.escape(marker)}")
        def strip_pair(match: re.Match[str]) -> str:
            inner = match.group(1)
            before = work[match.start()-1] if match.start() else " "
            after = work[match.end()] if match.end() < len(work) else " "
            if not inner or inner[0].isspace() or inner[-1].isspace():
                return match.group(0)
            # Underscores inside identifiers are literal. Emphasis markers need
            # punctuation/space boundaries on both sides.
            if before.isalnum() or after.isalnum():
                return match.group(0)
            if before == "\\" or after == "\\":
                return match.group(0)
            return inner
        work = pattern.sub(strip_pair, work)

    work = re.sub(r"\\([\\`*_[\]{}()#+.!|>-])", r"\1", work)
    work = work.strip("\n")
    return re.sub(r"\x00(\d+)\x00", lambda m: protected[int(m.group(1))], work)
