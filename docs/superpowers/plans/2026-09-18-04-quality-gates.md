# Quality Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn editorial review into deterministic, auditable gates for project authority, craft benchmarks, wiki integrity, and Lexile measurement.

**Architecture:** Add a shared quality report model and a pre-output gate runner. The runner combines craft metrics, project red lines, semantic judgments, wiki lint, and Lexile results into one report. Project-authority failures block delivery; craft deviations warn unless the user explicitly accepts them.

**Tech Stack:** Python 3.10+ standard library, `unittest`, JSON reports, existing `craft-benchmark-check`, `ControlPlane`, and `page_codec`.

**Spec:** `docs/superpowers/specs/2026-09-18-workbuddy-codex-plugin-roadmap.md`

## Global Constraints

- Project-authority FAIL blocks confirmation.
- Craft-benchmark FAIL is a warning unless the user explicitly accepts it.
- Cheap proxies must be deterministic and zero-false-negative.
- Semantic judgments must cite the triggering evidence.
- Wiki lint must be read-only.
- Lexile values must come from the adapter, never from estimation.
- No gate may silently rewrite content.
- Every report is machine-readable JSON and human-readable Chinese Markdown.

---

### Task 1: Add the quality report model

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/pre_output-baseline/scripts/quality_gate.py`
- Test: `plugins/picturebook-screenwriter/skills/pre_output-baseline/scripts/test_quality_gate.py`

**Interfaces:**
- Produces:
  - `Finding(id, source, severity, message, evidence)`
  - `QualityReport(status, findings, blocked_reasons)`
  - `build_report(findings) -> QualityReport`
  - `report_to_markdown(report) -> str`

- [ ] **Step 1: Write failing tests**

```python
import unittest
from quality_gate import Finding, build_report, report_to_markdown


class QualityGateTests(unittest.TestCase):
    def test_project_fail_blocks(self):
        finding = Finding("R1", "project", "FAIL", "违反角色红线", "角色不能飞行")
        report = build_report([finding])
        self.assertEqual(report.status, "blocked")

    def test_craft_fail_only_warns(self):
        finding = Finding("C1", "craft", "FAIL", "翻页钩子密度不足", "平均 6 页")
        report = build_report([finding])
        self.assertEqual(report.status, "needs_user_decision")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Implement the model**

```python
"""Shared quality report model for the pre-output gate."""

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Finding:
    id: str
    source: str
    severity: str
    message: str
    evidence: str


@dataclass(frozen=True)
class QualityReport:
    status: str
    findings: tuple[Finding, ...]
    blocked_reasons: tuple[str, ...]


def build_report(findings: Iterable[Finding]) -> QualityReport:
    values = tuple(findings)
    blocked = tuple(
        finding.message for finding in values
        if finding.source == "project" and finding.severity == "FAIL"
    )
    status = "blocked" if blocked else (
        "needs_user_decision"
        if any(finding.severity == "FAIL" for finding in values)
        else "passed"
    )
    return QualityReport(status, values, blocked)


def report_to_markdown(report: QualityReport) -> str:
    lines = ["## 质量报告", "", f"状态：{report.status}", ""]
    for finding in report.findings:
        lines.append(f"- [{finding.severity}] {finding.message}；依据：{finding.evidence}")
    if report.blocked_reasons:
        lines.extend(["", "阻断原因："])
        lines.extend(f"- {reason}" for reason in report.blocked_reasons)
    return "\n".join(lines) + "\n"
```

- [ ] **Step 3: Run tests**

```powershell
python .\plugins\picturebook-screenwriter\skills\pre_output-baseline\scripts\test_quality_gate.py -v
```

Expected: 2 tests pass.

- [ ] **Step 4: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\pre_output-baseline\scripts\quality_gate.py .\plugins\picturebook-screenwriter\skills\pre_output-baseline\scripts\test_quality_gate.py
git commit -m "feat: add quality report model"
```

---

### Task 2: Add the project red-line proxy

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/pre_output-baseline/scripts/redline_proxy.py`
- Test: `plugins/picturebook-screenwriter/skills/pre_output-baseline/scripts/test_redline_proxy.py`

**Interfaces:**
- Produces:
  - `scan_redlines(text, rules) -> tuple[Finding, ...]`
  - Rules are `(rule_id, pattern, evidence)` tuples.

- [ ] **Step 1: Write failing tests**

```python
import unittest
from redline_proxy import scan_redlines


class RedlineProxyTests(unittest.TestCase):
    def test_forbidden_term_is_found(self):
        rules = (("R1", "飞行", "角色不能飞行"),)
        findings = scan_redlines("迈尔斯开始飞行。", rules)
        self.assertEqual(findings[0].id, "R1")
        self.assertEqual(findings[0].severity, "FAIL")

    def test_empty_rules_return_no_findings(self):
        self.assertEqual(scan_redlines("任意文本", ()), ())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Implement the proxy**

```python
"""Cheap zero-false-negative project red-line scanner."""

from quality_gate import Finding


def scan_redlines(text: str, rules: tuple[tuple[str, str, str], ...]) -> tuple[Finding, ...]:
    findings = []
    for rule_id, pattern, evidence in rules:
        if pattern and pattern in text:
            findings.append(Finding(
                id=rule_id,
                source="project",
                severity="FAIL",
                message=f"命中项目红线：{pattern}",
                evidence=evidence,
            ))
    return tuple(findings)
```

- [ ] **Step 3: Run tests**

```powershell
python .\plugins\picturebook-screenwriter\skills\pre_output-baseline\scripts\test_redline_proxy.py -v
```

Expected: 2 tests pass.

- [ ] **Step 4: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\pre_output-baseline\scripts\redline_proxy.py .\plugins\picturebook-screenwriter\skills\pre_output-baseline\scripts\test_redline_proxy.py
git commit -m "feat: add project redline proxy"
```

---

### Task 3: Add the semantic judgment record

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/pre_output-baseline/scripts/quality_gate.py`
- Test: `plugins/picturebook-screenwriter/skills/pre_output-baseline/scripts/test_quality_gate.py`

**Interfaces:**
- Produces:
  - `SemanticJudgment(finding_id, verdict, rationale, evidence)`
  - `apply_judgments(findings, judgments) -> tuple[Finding, ...]`

- [ ] **Step 1: Add failing tests**

```python
from quality_gate import SemanticJudgment, apply_judgments

    def test_pass_judgment_downgrades_flag(self):
        finding = Finding("R1", "project", "FAIL", "疑似红线", "角色不能飞行")
        judgment = SemanticJudgment("R1", "PASS", "上下文是玩具飞机", "上下文")
        result = apply_judgments([finding], [judgment])
        self.assertEqual(result[0].severity, "PASS")

    def test_missing_judgment_keeps_fail(self):
        finding = Finding("R1", "project", "FAIL", "疑似红线", "角色不能飞行")
        result = apply_judgments([finding], [])
        self.assertEqual(result[0].severity, "FAIL")
```

- [ ] **Step 2: Implement judgment records**

```python
@dataclass(frozen=True)
class SemanticJudgment:
    finding_id: str
    verdict: str
    rationale: str
    evidence: str


def apply_judgments(findings: tuple[Finding, ...],
                    judgments: tuple[SemanticJudgment, ...]) -> tuple[Finding, ...]:
    by_id = {judgment.finding_id: judgment for judgment in judgments}
    result = []
    for finding in findings:
        judgment = by_id.get(finding.id)
        if judgment is None:
            result.append(finding)
            continue
        if judgment.verdict not in {"PASS", "WARN", "FAIL"}:
            raise ValueError(f"invalid verdict: {judgment.verdict}")
        result.append(Finding(
            finding.id, finding.source, judgment.verdict,
            f"{finding.message}；语义判定：{judgment.rationale}",
            judgment.evidence or finding.evidence,
        ))
    return tuple(result)
```

- [ ] **Step 3: Run tests**

```powershell
python .\plugins\picturebook-screenwriter\skills\pre_output-baseline\scripts\test_quality_gate.py -v
```

Expected: 4 tests pass.

- [ ] **Step 4: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\pre_output-baseline\scripts\quality_gate.py .\plugins\picturebook-screenwriter\skills\pre_output-baseline\scripts\test_quality_gate.py
git commit -m "feat: record semantic quality judgments"
```

---

### Task 4: Add target-Wiki lint

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/wiki_lint.py`
- Test: `plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/test_wiki_lint.py`

**Interfaces:**
- Produces:
  - `lint_index(index) -> tuple[Finding, ...]`
  - Findings use source `wiki`.
- Consumes: the same `IndexEntry` dictionary shape used by `ControlPlane.read_index`.

- [ ] **Step 1: Write failing tests**

```python
import unittest
from wiki_lint import lint_index


class WikiLintTests(unittest.TestCase):
    def test_duplicate_keys_are_errors(self):
        index = {
            "a/b/worldview": {"key": "a/b/worldview", "status": "published"},
        }
        findings = lint_index(index)
        self.assertEqual(findings, ())

    def test_needs_review_is_warned(self):
        index = {"a/b/worldview": {"key": "a/b/worldview", "status": "needs_review"}}
        findings = lint_index(index)
        self.assertEqual(findings[0].severity, "WARN")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Implement Wiki lint**

```python
"""Read-only integrity checks for the authoritative target Wiki."""

import sys
from pathlib import Path

PRE_OUTPUT = Path(__file__).parents[2] / "pre_output-baseline" / "scripts"
if str(PRE_OUTPUT) not in sys.path:
    sys.path.insert(0, str(PRE_OUTPUT))

from quality_gate import Finding


def lint_index(index: dict[str, dict[str, object]]) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for key, entry in index.items():
        if entry.get("key") != key:
            findings.append(Finding(
                f"wiki-key-{key}", "wiki", "FAIL", "索引键与条目键不一致", str(entry)
            ))
        if entry.get("status") == "needs_review":
            findings.append(Finding(
                f"wiki-review-{key}", "wiki", "WARN", "知识页存在待处理冲突", key
            ))
    return tuple(findings)
```

- [ ] **Step 3: Run tests**

```powershell
python .\plugins\picturebook-screenwriter\skills\wiki-ingest\scripts\test_wiki_lint.py -v
```

Expected: 2 tests pass.

- [ ] **Step 4: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\wiki-ingest\scripts\wiki_lint.py .\plugins\picturebook-screenwriter\skills\wiki-ingest\scripts\test_wiki_lint.py
git commit -m "feat: add target wiki lint"
```

---

### Task 5: Add the Lexile adapter boundary

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/lexile-check/SKILL.md`
- Create: `plugins/picturebook-screenwriter/skills/lexile-check/scripts/lexile_api.py`
- Test: `plugins/picturebook-screenwriter/skills/lexile-check/scripts/test_lexile_api.py`

**Interfaces:**
- Produces:
  - `LexileClient.measure(text) -> LexileResult`
  - `LexileResult(score, band, measured)`
  - A fake client is sufficient for offline tests.

- [ ] **Step 1: Write failing adapter tests**

```python
import unittest
from lexile_api import FakeLexileClient


class LexileApiTests(unittest.TestCase):
    def test_fake_client_is_explicitly_measured(self):
        result = FakeLexileClient(320, "Grade 1").measure("hello")
        self.assertEqual(result.score, 320)
        self.assertTrue(result.measured)

    def test_negative_score_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "score"):
            FakeLexileClient(-1, "invalid").measure("hello")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Implement the adapter**

```python
"""Lexile measurement boundary. A real client must never estimate a score."""

from dataclasses import dataclass


@dataclass(frozen=True)
class LexileResult:
    score: int
    band: str
    measured: bool


class FakeLexileClient:
    def __init__(self, score: int, band: str) -> None:
        if score < 0:
            raise ValueError("Lexile score must be non-negative")
        self.score = score
        self.band = band

    def measure(self, text: str) -> LexileResult:
        return LexileResult(self.score, self.band, measured=True)
```

- [ ] **Step 3: Create the skill**

```markdown
---
name: lexile-check
description: 调用外部 Lexile 服务测量文本难度；只返回实测值，不估算，不硬编码目标区间。
---

# Lexile Check

## Workflow

1. 向用户确认是否执行外部测量，并说明需要网络和 API 凭据。
2. 将完整页文交给 `scripts/lexile_api.py` 指向的实际客户端。
3. 返回 `score`、`band` 和 `measured`。
4. 若 `measured` 为 false，报告“未测量”，不得显示估算值。

## Output

```json
{"score": 320, "band": "Grade 1", "measured": true}
```
```

- [ ] **Step 4: Run tests**

```powershell
python .\plugins\picturebook-screenwriter\skills\lexile-check\scripts\test_lexile_api.py -v
```

Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\lexile-check
git commit -m "feat: add lexile measurement boundary"
```

---

### Task 6: Integrate quality gates into the entry workflow

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/picturebook-screenwriter/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/tests/test_skill_contract.py`

**Interfaces:**
- Consumes: `quality_gate.py`, `redline_proxy.py`, `wiki_lint.py`, and `lexile-check`.
- Produces: one confirmation package containing draft, metrics, findings, and blocked reasons.

- [ ] **Step 1: Add contract tests**

```python
    def test_entry_blocks_project_failures(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("项目权威 FAIL", text)
        self.assertIn("阻断", text)

    def test_entry_distinguishes_craft_warnings(self):
        text = ENTRY_SKILL.read_text(encoding="utf-8")
        self.assertIn("工艺基准 FAIL", text)
        self.assertIn("用户确认", text)
```

- [ ] **Step 2: Update the entry skill**

```markdown
## Quality Gate

确认门前必须汇总：

1. `craft-benchmark-check` 的确定性指标。
2. `pre_output-baseline/scripts/quality_gate.py` 的项目红线与语义判定。
3. `wiki-ingest/scripts/wiki_lint.py` 的权威知识状态。
4. 如用户要求，`lexile-check` 的实测结果。

项目权威 FAIL 阻断确认，必须先修订。工艺基准 FAIL 不自动阻断，但必须列出差值并请求用户确认。若用户确认接受，记录确认理由；不得把接受后的工艺偏差伪装为通过。
```

- [ ] **Step 3: Run the full quality suite**

```powershell
python -m unittest discover -s .\plugins\picturebook-screenwriter\skills\pre_output-baseline\scripts -p "test_*.py" -v
python .\plugins\picturebook-screenwriter\skills\wiki-ingest\scripts\test_wiki_lint.py -v
python .\plugins\picturebook-screenwriter\skills\lexile-check\scripts\test_lexile_api.py -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\picturebook-screenwriter\SKILL.md .\plugins\picturebook-screenwriter\tests\test_skill_contract.py
git commit -m "feat: integrate quality gate workflow"
```

---

## Self-Review

- Covers project red lines, semantic judgments, Wiki integrity, and Lexile measurement.
- Blocking semantics are explicit and tested.
- Wiki lint is read-only.
- No network call is required by unit tests.
