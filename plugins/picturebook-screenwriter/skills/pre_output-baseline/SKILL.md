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

项目权威触犯阻断：`project_authority` 来源的 finding 一律阻断，不看它记录了什么严重度（`apply_judgments()` 不会降级权威 finding）。工艺基准 FAIL 不阻断但必须向用户说明。

## Jev 辅助预筛（仅在用户选择 Jev 辅助路径时）

用户选择 Jev 辅助时，`scripts/screening_runner.py` 会：

1. 用红线词表为**每一条活动红线**对每个**有文字的**页面窗口生成原子 Noul 问题——不是只审正则已命中的 FLAG。无文字页不生成项，也不会计入 clear。
2. 用 `scripts/page_quality.py` 为每页最多五个质量维度生成原子 Noul 问题；末页豁免翻页动力。字数、句数与单行最大重复次数（`char_count` / `sentence_count` / `max_line_repeat`）由代码算好放进 state，不让 Jev 计数。
3. 调用共享运行器，按 `decision-policies.json` 的路由规则把每项判成 `screened_clear`、`escalate_llm` 或 `runtime_failure`。
4. 只把高风险、灰区、异常项与代理冲突组装成升级包交给普通 LLM。

**`screened_clear` 绝不来自失败。** 任何 API 失败、解析失败、答案缺失或 `outcome_unknown` 都变成 `escalate_llm` 或 `runtime_failure`。

**`experimental` 期间 `screened_clear` 不缩减复核范围。** 它只产生对比数据；只有 `calibrated` 的 operation 才允许跳过该项复核，这个判断由 `screening.may_skip_llm_review()` 执行。

阈值不写死在代码里：按 `references/calibration-samples.md` 跑真实测量，再用 `scripts/calibrate.py` 选阈值，然后才改 `calibration_status`。

### 运行预筛

```bash
python scripts/screening_cli.py \
    --script <逐页脚本 draft.md> \
    --bundle <authority bundle.json> \
    --run-dir <run_dir> \
    [--run-id <run_id>] \
    [--age-band 3-6] \
    [--window-width 1] \
    [--escalation-out <升级包.json>] \
    [--report-out <报告.json>]
```

- `--script` 指向被审的逐页脚本；红线词表由 `--bundle` 里的权威页面重建，字面扫描只补充候选证据，不缩小提问范围。
- `--escalation-out` 写出的升级包含非 `screened_clear` 的维度（高风险、灰区、异常与失败），**外加代理冲突条目**（字面扫描命中、Jev 却判 clear，`reason: proxy_conflict`；`scope: window` 指某个页窗被问过该红线，`scope: draft` 指该规则根本没被问过），交给普通 LLM 复核；`--report-out` 写出同一份报告，含比率、路由、`blocked_records`、`proxy_conflicts` 与 `warnings`。
- 报告里的 `catalog_gap=true` 表示红线词表为空——那是词表缺口，不是"草稿没有红线"；`page_count>0` 而 `item_count=0` 表示页面全部无文字，这两条都会在 `warnings` 里写明，空报告不是"通过"。
- `calibration_status` 仍是 `experimental` 时 `may_skip_llm_review_count` 恒为 0：`screened_clear` 不缩减普通 LLM 复核范围；即使已 `calibrated`，只要存在代理冲突，该计数也会归零——两路信号正好相反的那一项不能靠"clear"跳过。
- 读不成逐页脚本的文件（页面表解析不出任何行）与读不成证据包的 bundle，都在发出任何请求前以 JSON 错误退出（退出码 1），不会留下升级包。
- 预筛本身跑完就返回 0：即使所有维度都在等待 key 或已失败，结论也只在 `results[].status`、`summary` 与 `warnings` 里。只检查退出码会把它误读成"已通过"。
- run id 默认取 `--run-dir` 的目录名；目录名不符合 `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`（含点号、空格、中文或过长）时命令拒绝执行，并提示显式传入 `--run-id`。

## Output

输出 FLAG 表格、判定结果、阻断建议和来源；Jev 辅助路径额外输出预筛比率（clear / 升级 / 失败）与升级包。

## Not In Scope

- 本技能不生成图片，不写入文件，不跳过权威 FAIL。
- 本技能不替代 quality-baseline 的读者视角和跨产物一致性检查。
- 本技能不覆盖"禁忌词、称呼一致性和插画描述"的零假阴性扫描——**尚未实现**，只有项目红线的字面扫描存在。不要把它当作已完成的能力。
- 句长与标点模式事实尚未提供：`scripts/page_quality.py` 的 `page_facts()` 目前只产出 `char_count`、`sentence_count`、`max_line_repeat` 三项。
