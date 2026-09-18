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

## Not In Scope

- 本技能不生成图片，不写入文件，不跳过权威 FAIL。
- 本技能不替代 quality-baseline 的读者视角和跨产物一致性检查。
