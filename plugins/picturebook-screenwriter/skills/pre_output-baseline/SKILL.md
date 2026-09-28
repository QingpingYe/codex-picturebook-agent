---
name: pre_output-baseline
description: pre_output 槽位的全产物类型兜底技能，先做廉价代理扫描，再做语义判定。
---

# Pre Output Baseline

## Stage 1: Cheap Proxy

使用 `../craft-benchmark-check/SKILL.md` 获取确定性指标，并把项目红线字面命中扫出来。

红线词表来自权威知识：`scripts/redline_catalog.py` 从约束页面的 `machine-data: redline_terms` / `banned_terms` 块重建词表，块缺失时回退解析禁止章节里被反引号或引号标出的片段。词表为空时必须显式报告缺口——那是"本项目没有登记红线"，不是"草稿没有红线"。

扫描产出的 `Finding` 使用 `source="project_proxy"`：它表示"这段文字命中了某个被禁的字面词"，是**候选**，不是判定。

## Stage 2: Semantic Review

对每个 FLAG 逐条判定 `PASS`、`WARN` 或 `FAIL`，必须引用权威来源。

判定结果只对 `project_proxy` 生效：`apply_judgments()` **不会**改变 `project_authority` 的 severity，因为权威触犯是草稿的既成事实，不是可以复核掉的看法。`FAIL` 会把代理候选经 `promote_confirmed_redlines()` 提升为 `project_authority`，这是唯一能产生阻断判定的路径。

项目权威 FAIL 阻断，工艺基准 FAIL 不阻断但必须向用户说明。

## Jev 辅助预筛（仅在用户选择 Jev 辅助路径时）

用户选择 Jev 辅助时，`scripts/screening_runner.py` 会：

1. 用红线词表为**每一条活动红线**对每个页面窗口生成原子 Noul 问题——不是只审正则已命中的 FLAG。
2. 用 `scripts/page_quality.py` 为每页最多五个质量维度生成原子 Noul 问题；末页豁免翻页动力。字数、句长、重复次数由代码算好放进 state，不让 Jev 计数。
3. 调用共享运行器，按 `decision-policies.json` 的路由规则把每项判成 `screened_clear`、`escalate_llm` 或 `runtime_failure`。
4. 只把高风险、灰区、异常项与代理冲突组装成升级包交给普通 LLM。

**`screened_clear` 绝不来自失败。** 任何 API 失败、解析失败、答案缺失或 `outcome_unknown` 都变成 `escalate_llm` 或 `runtime_failure`。

**`experimental` 期间 `screened_clear` 不缩减复核范围。** 它只产生对比数据；只有 `calibrated` 的 operation 才允许跳过该项复核，这个判断由 `screening.may_skip_llm_review()` 执行。

阈值不写死在代码里：按 `references/calibration-samples.md` 跑真实测量，再用 `scripts/calibrate.py` 选阈值，然后才改 `calibration_status`。

## Output

输出 FLAG 表格、判定结果、阻断建议和来源；Jev 辅助路径额外输出预筛比率（clear / 升级 / 失败）与升级包。

## Not In Scope

- 本技能不生成图片，不写入文件，不跳过权威 FAIL。
- 本技能不替代 quality-baseline 的读者视角和跨产物一致性检查。
- 本技能不覆盖"禁忌词、称呼一致性和插画描述"的零假阴性扫描——**尚未实现**，只有项目红线的字面扫描存在。不要把它当作已完成的能力。
