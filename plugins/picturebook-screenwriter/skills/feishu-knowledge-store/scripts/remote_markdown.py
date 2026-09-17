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
