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

## Not In Scope

- 本技能不做表达层整体打磨，不执行最终质量门，不写入文件。
- 本技能不派发子代理，不生成图片，不替代权威知识缺失时的回问。
