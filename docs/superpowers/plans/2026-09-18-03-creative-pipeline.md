# Creative Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the WorkBuddy creative pipeline into Codex-native skills and a deterministic slot resolver.

**Architecture:** Add lightweight planning and the five baseline-slot skills, then implement a small resolver that maps artifact type, series, and project to the correct skill. The entry skill consumes the resolver, so slot selection is testable instead of implicit prose.

**Tech Stack:** Python 3.10+ standard library, `unittest`, Markdown skills, Codex plugin manifest.

**Spec:** `docs/superpowers/specs/2026-09-18-workbuddy-codex-plugin-roadmap.md`

## Global Constraints

- All user-visible skill text is Chinese.
- No WorkBuddy-only runtime terms may appear as executable instructions.
- Slot selection must be deterministic and testable.
- Baseline skills are defaults, not project-specific rules.
- No file write occurs without explicit user approval or an explicit export request.
- Lightweight mode is dialog-only.
- Every skill must state what it does not do, not only what it does.

---

### Task 1: Implement the slot resolver

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/picturebook-screenwriter/scripts/slot_resolver.py`
- Test: `plugins/picturebook-screenwriter/skills/picturebook-screenwriter/scripts/test_slot_resolver.py`

**Interfaces:**
- Produces:
  - `SlotRequest(artifact_type, series_id, project_id)`
  - `resolve_slot(request, slot) -> str`
  - `SLOTS = ("pre_create", "in_create", "post_create", "pre_output", "quality")`

- [ ] **Step 1: Write the failing resolver tests**

```python
import unittest
from slot_resolver import resolve_slot, SlotRequest


class SlotResolverTests(unittest.TestCase):
    def test_baseline_is_selected(self):
        request = SlotRequest("script", "海外绘本", "小老鼠迈尔斯")
        self.assertEqual(resolve_slot(request, "pre_create"), "pre_create-baseline")

    def test_invalid_slot_is_rejected(self):
        request = SlotRequest("script", "海外绘本", "小老鼠迈尔斯")
        with self.assertRaisesRegex(ValueError, "slot"):
            resolve_slot(request, "unknown")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Implement the resolver**

```python
"""Deterministic baseline-slot resolution for the editorial workflow."""

from dataclasses import dataclass


SLOTS = ("pre_create", "in_create", "post_create", "pre_output", "quality")
ARTIFACT_TYPES = ("positioning", "topic", "worldview", "character", "outline", "script")


@dataclass(frozen=True)
class SlotRequest:
    artifact_type: str
    series_id: str | None = None
    project_id: str | None = None

    def __post_init__(self) -> None:
        if self.artifact_type not in ARTIFACT_TYPES:
            raise ValueError(f"invalid artifact_type: {self.artifact_type}")


def resolve_slot(request: SlotRequest, slot: str) -> str:
    if slot not in SLOTS:
        raise ValueError(f"invalid slot: {slot}")
    # Phase 3 intentionally installs only the baseline tier. Project and series
    # tiers are added later without changing this return contract.
    return f"{slot}-baseline"
```

- [ ] **Step 3: Run tests**

```powershell
python .\plugins\picturebook-screenwriter\skills\picturebook-screenwriter\scripts\test_slot_resolver.py -v
```

Expected: 2 tests pass.

- [ ] **Step 4: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\picturebook-screenwriter\scripts\slot_resolver.py .\plugins\picturebook-screenwriter\skills\picturebook-screenwriter\scripts\test_slot_resolver.py
git commit -m "feat: add deterministic slot resolver"
```

---

### Task 2: Port lightweight story planning

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/story-planning/SKILL.md`
- Test: `plugins/picturebook-screenwriter/tests/test_creative_pipeline_skills.py`

**Interfaces:**
- Produces: `story-planning` skill, triggered only by explicit lightweight mode.
- Consumes: `knowledge-loader`.

- [ ] **Step 1: Add the failing skill test**

```python
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
STORY = ROOT / "skills" / "story-planning" / "SKILL.md"


class StoryPlanningTests(unittest.TestCase):
    def test_explicit_lightweight_trigger_only(self):
        text = STORY.read_text(encoding="utf-8")
        self.assertIn("显式", text)
        self.assertIn("轻量", text)
        self.assertIn("不落盘", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Create the skill**

```markdown
---
name: story-planning
description: 轻量创意构思技能。仅当用户显式要求轻量、脑暴或只要构思时使用；最小检索后快速产出候选创意，不派发子代理，不落盘。
---

# Story Planning

## Trigger

只有用户显式使用“轻量”、“脑暴”、“只要构思”或“先不用写完整脚本”时才启用本技能。没有显式信号时，一律进入完整编辑流程，不得自行降级。

## Workflow

1. 读取用户主题、目标年龄段、已有角色或世界观线索。
2. 通过 `../knowledge-loader/SKILL.md` 做最小检索，优先查找角色、世界观、已有大纲和创意登记册。
3. 若没有匹配证据，明确说明“资料库中无此项目已有内容”，再继续构思。
4. 输出 3-5 个候选创意，每个包含：一句话核心巧思、主角动作、情感落点、可展开结构。
5. 发送前自检：数量匹配、无重复、格式一致、来源已标注。
6. 本技能只输出对话内容，不写任何本地文件。

## Output Contract

- 全程中文。
- 每个候选创意必须可独立展开。
- 不新增角色、世界观或数值约束。
- 末尾固定说明：`[轻量创意模式] 如需展开为完整脚本，请告知，将进入完整编辑流程。`
```

- [ ] **Step 3: Run the tests**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_creative_pipeline_skills.py -v
```

Expected: 1 test passes.

- [ ] **Step 4: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\story-planning\SKILL.md .\plugins\picturebook-screenwriter\tests\test_creative_pipeline_skills.py
git commit -m "feat: port lightweight story planning"
```

---

### Task 3: Port the pre-create baseline

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/pre_create-baseline/SKILL.md`
- Test: `plugins/picturebook-screenwriter/tests/test_creative_pipeline_skills.py`

**Interfaces:**
- Produces: `pre_create-baseline`.
- Consumes: the brief and evidence bundle.

- [ ] **Step 1: Add the test**

```python
PRE_CREATE = ROOT / "skills" / "pre_create-baseline" / "SKILL.md"

    def test_pre_create_reports_gaps_not_defaults(self):
        text = PRE_CREATE.read_text(encoding="utf-8")
        self.assertIn("缺口", text)
        self.assertIn("不猜默认值", text)
```

- [ ] **Step 2: Create the skill**

```markdown
---
name: pre_create-baseline
description: pre_create 槽位的全产物类型兜底技能，用于复核简报完整性；只报告缺口，不猜默认值。
---

# Pre Create Baseline

## Scope

适用于 `positioning`、`topic`、`worldview`、`character`、`outline` 和 `script`。本技能是默认兜底，不承载项目专属规则。

## Workflow

1. 读取用户已提供的简报要素：目标年龄段、主题方向、页数预期、文字量、文体偏好、禁忌清单、参考作品。
2. 对照权威知识中的 `content-spec`、世界观、角色和纠正登记册。
3. 对每个要素输出“有来源”或“缺口”。
4. 缺口必须交回用户补充或显式豁免；本技能不猜默认值。

## Output

```markdown
## pre_create 基线检查

- 目标年龄段：有来源 / 缺口
- 主题方向：有来源 / 缺口
- 页数预期：有来源 / 缺口
```
```

- [ ] **Step 3: Run tests**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_creative_pipeline_skills.py -v
```

Expected: 2 tests pass.

- [ ] **Step 4: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\pre_create-baseline\SKILL.md .\plugins\picturebook-screenwriter\tests\test_creative_pipeline_skills.py
git commit -m "feat: port pre create baseline"
```

---

### Task 4: Port the in-create baseline

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/in_create-baseline/SKILL.md`
- Test: `plugins/picturebook-screenwriter/tests/test_creative_pipeline_skills.py`

**Interfaces:**
- Produces: `in_create-baseline`.
- Consumes: `text-craft` references and authority evidence.

- [ ] **Step 1: Add the test**

```python
IN_CREATE = ROOT / "skills" / "in_create-baseline" / "SKILL.md"

    def test_in_create_requires_structure_before_body(self):
        text = IN_CREATE.read_text(encoding="utf-8")
        self.assertIn("结构骨架", text)
        self.assertIn("边界卡", text)
        self.assertIn("text-craft", text)
```

- [ ] **Step 2: Create the skill**

```markdown
---
name: in_create-baseline
description: in_create 槽位的全产物类型兜底技能，先装载工艺方法、结构骨架与边界卡，再进入正文创作。
---

# In Create Baseline

## Craft Loading

创作前读取 `../text-craft/SKILL.md` 及相关引用：

- `craft-principles.md`
- `technique-cards.md`
- `craft-baselines.md`
- `preflight-checklist.md`

## Structure Gate

写正文前必须先确定：

1. 产物类型
2. 叙事母弧线或结构骨架
3. 关键情感落点
4. 页数或段落数约束
5. 禁止事项和权威边界

任一项缺失时，先回问用户或读取权威知识，不得先写后补。

## Boundary Card

把权威知识中的硬约束整理为：

```markdown
## 边界卡

- 世界观：
- 角色边界：
- 数值约束：
- 禁止事项：
```
```

- [ ] **Step 3: Run tests**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_creative_pipeline_skills.py -v
```

Expected: 3 tests pass.

- [ ] **Step 4: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\in_create-baseline\SKILL.md .\plugins\picturebook-screenwriter\tests\test_creative_pipeline_skills.py
git commit -m "feat: port in create baseline"
```

---

### Task 5: Port the post-create baseline

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/post_create-baseline/SKILL.md`
- Test: `plugins/picturebook-screenwriter/tests/test_creative_pipeline_skills.py`

**Interfaces:**
- Produces: `post_create-baseline`.
- Consumes: a completed draft and `text-craft`.

- [ ] **Step 1: Add the test**

```python
POST_CREATE = ROOT / "skills" / "post_create-baseline" / "SKILL.md"

    def test_post_create_is_polish_not_rewrite(self):
        text = POST_CREATE.read_text(encoding="utf-8")
        self.assertIn("只做打磨", text)
        self.assertIn("不得新增", text)
```

- [ ] **Step 2: Create the skill**

```markdown
---
name: post_create-baseline
description: post_create 槽位的全产物类型兜底技能，对完整草稿做表达层打磨，不新增情节元素。
---

# Post Create Baseline

## Scope

作用于完整草稿，只做表达层打磨。不得新增角色、场景、道具、情节转折或未经确认的数值约束。

## Checks

1. 语言密度：删冗余、去重复。
2. 展示而非讲述：把直接心理陈述改为可观察动作。
3. 术语一致性：角色、场景、道具称呼统一。
4. 节奏：只做微调，不重排结构。
5. 数值边界：润色后重新对照权威 `content-spec`。

## Output

在草稿后追加“打磨说明”，列出修改点与理由。
```

- [ ] **Step 3: Run tests**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_creative_pipeline_skills.py -v
```

Expected: 4 tests pass.

- [ ] **Step 4: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\post_create-baseline\SKILL.md .\plugins\picturebook-screenwriter\tests\test_creative_pipeline_skills.py
git commit -m "feat: port post create baseline"
```

---

### Task 6: Port the pre-output and quality baselines

**Files:**
- Create: `plugins/picturebook-screenwriter/skills/pre_output-baseline/SKILL.md`
- Create: `plugins/picturebook-screenwriter/skills/quality-baseline/SKILL.md`
- Test: `plugins/picturebook-screenwriter/tests/test_creative_pipeline_skills.py`

**Interfaces:**
- Produces: `pre_output-baseline` and `quality-baseline` contracts.
- Consumes: `craft-benchmark-check`, authority evidence, and the completed draft.

- [ ] **Step 1: Add tests**

```python
PRE_OUTPUT = ROOT / "skills" / "pre_output-baseline" / "SKILL.md"
QUALITY = ROOT / "skills" / "quality-baseline" / "SKILL.md"

    def test_pre_output_uses_two_stage_review(self):
        text = PRE_OUTPUT.read_text(encoding="utf-8")
        self.assertIn("廉价代理", text)
        self.assertIn("语义判定", text)

    def test_quality_baseline_complements_not_duplicates(self):
        text = QUALITY.read_text(encoding="utf-8")
        self.assertIn("不重复", text)
        self.assertIn("跨产物一致性", text)
```

- [ ] **Step 2: Create both skills**

`pre_output-baseline/SKILL.md`:

```markdown
---
name: pre_output-baseline
description: pre_output 槽位的全产物类型兜底技能，先做廉价代理扫描，再做语义判定。
---

# Pre Output Baseline

## Stage 1: Cheap Proxy

使用 `../craft-benchmark-check/SKILL.md` 获取确定性指标，并对权威红线、禁忌词、称呼一致性和插画描述做零假阴性扫描。

## Stage 2: Semantic Review

对每个 FLAG 逐条判定 `PASS`、`WARN` 或 `FAIL`，必须引用权威来源。项目权威 FAIL 阻断，工艺基准 FAIL 不阻断但必须向用户说明。

## Output

输出 FLAG 表格、判定结果、阻断建议和来源。
```

`quality-baseline/SKILL.md`:

```markdown
---
name: quality-baseline
description: quality 槽位的全产物类型兜底技能，补充读者视角、朗读测试、跨产物一致性和漏检补位。
---

# Quality Baseline

## Checks

1. 读者视角：能理解、会被吸引、愿意翻页。
2. 朗读测试：拗口点、可预测性、韵脚与叠词。
3. 跨产物一致性：比对已提供的世界观、角色、大纲和脚本。
4. 漏检补位：只复核自上次扫描后变化的部分。

本技能不重复已完成的确定型指标和语义判定，只补足它们未覆盖的视角。
```

- [ ] **Step 3: Run tests**

```powershell
python .\plugins\picturebook-screenwriter\tests\test_creative_pipeline_skills.py -v
```

Expected: 6 tests pass.

- [ ] **Step 4: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\pre_output-baseline\SKILL.md .\plugins\picturebook-screenwriter\skills\quality-baseline\SKILL.md .\plugins\picturebook-screenwriter\tests\test_creative_pipeline_skills.py
git commit -m "feat: port output and quality baselines"
```

---

### Task 7: Integrate the five slots into the entry workflow

**Files:**
- Modify: `plugins/picturebook-screenwriter/skills/picturebook-screenwriter/SKILL.md`
- Modify: `plugins/picturebook-screenwriter/tests/test_creative_pipeline_skills.py`

**Interfaces:**
- Consumes: `slot_resolver.py`.
- Produces: a six-artifact editorial workflow with deterministic slot use.

- [ ] **Step 1: Add the integration test**

```python
    def test_entry_uses_all_five_slots(self):
        text = (ROOT / "skills" / "picturebook-screenwriter" / "SKILL.md").read_text(encoding="utf-8")
        for slot in ("pre_create", "in_create", "post_create", "pre_output", "quality"):
            self.assertIn(f"{slot}-baseline", text)
```

- [ ] **Step 2: Update the entry workflow**

Add:

```markdown
## Editorial Slots

1. `pre_create-baseline`：复核简报与权威知识缺口。
2. `in_create-baseline`：装载工艺方法、结构骨架和边界卡。
3. `post_create-baseline`：对完整草稿做表达层打磨。
4. `pre_output-baseline`：执行廉价代理扫描和语义判定。
5. `quality-baseline`：补读者视角、朗读测试、跨产物一致性和漏检复查。

所有槽位通过 `scripts/slot_resolver.py` 选择，当前默认使用 baseline 层。未来新增项目层或系列层时，不得改变入口契约。
```

- [ ] **Step 3: Run all Phase 3 tests**

```powershell
python .\plugins\picturebook-screenwriter\skills\picturebook-screenwriter\scripts\test_slot_resolver.py -v
python .\plugins\picturebook-screenwriter\tests\test_creative_pipeline_skills.py -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```powershell
git add .\plugins\picturebook-screenwriter\skills\picturebook-screenwriter\SKILL.md .\plugins\picturebook-screenwriter\tests\test_creative_pipeline_skills.py
git commit -m "feat: integrate creative pipeline slots"
```

---

## Self-Review

- Covers lightweight planning and all five baseline slots.
- Slot selection is executable, not only descriptive.
- Every skill has an offline contract test.
- No task depends on quality-gate, art, or governance implementation.
