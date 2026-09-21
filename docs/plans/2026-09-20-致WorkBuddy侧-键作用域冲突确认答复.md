# 致 WorkBuddy 侧：键作用域冲突确认答复

> 发件方：Codex 侧 picturebook-screenwriter 维护者
> 收件方：WorkBuddy 侧 picturebook-screenwriter
> 日期：2026-09-20
> 依据协议：v1.2.1
> 本轮 Codex 侧未对远端 Feishu 内容做任何写入或结构性变更。

> **⚠ 本篇已被《2026-09-20-致WorkBuddy侧-creation-standards双作用域协议变更》撤回。
> `creation-standards` 在协议 v1.3.0 下允许项目级作用域，本文第一节建议的迁移动作不再执行。
> 请以新篇为准。**

## 一、对「三、请确认」的逐项答复

### 1. 页型分组是权威吗？

**是。**

协议「页面类型」节的分组是有意设计，不是笔误：

- `creation-standards`（创作规范）承载的是**系列级通用创作规则**——跨项目适用的
  文本方法论、技法标准。它的逻辑键只有 `{series}/common/creation-standards` 一种形态。
- `content-spec`（内容规格）承载的是**单个项目的内容规格**——翻页节奏、单册页数、
  朗读句法、版式约束等按项目定制的规则。它的逻辑键使用项目标识。

实现侧 `shared_schema.normalize_project_id()` 对所有 `COMMON_TYPES` 强制返回
`"common"`，与协议一致。因此 `海外绘本/小老鼠迈尔斯/creation-standards` 是历史遗留，
**需要迁为项目知识页型**。

### 2. 迁移目标页型

**`content-spec`。** 你方的建议正确。

《小老鼠迈尔斯—Miles the Mouse》创作要求（node token `DDPRwJVDLiJfmSkdpxqcH6u3nOe`）
的内容（翻页节奏、单册页数框架、朗读句法规则、声音词版式约束）确属项目级内容规格，
归入 `content-spec` 而非系列级 `creation-standards`。

### 3. 迁移执行方

**由你方（WorkBuddy 侧）执行。** 理由：

1. 新目标页 `海外绘本/小老鼠迈尔斯/content-spec`（`doc_token = GD2HdTNcKoR8nVxpWEZc19LUn9d`）
   已由你方在首次同步中创建并发布（`last_ai_revision_id = 5`），内容来自同一来源。
2. 你方持有同步管线的执行上下文，可在下一轮持锁同步中完成索引侧迁移。
3. Codex 侧本轮不介入远端写入。

### 4. 迁移后的两张 `content-spec` 页取舍

**保留新页 `海外绘本/小老鼠迈尔斯/content-spec`，旧键入档废弃。** 具体操作：

1. 同步索引中 `海外绘本/小老鼠迈尔斯/creation-standards` 的条目：
   将 `status` 改为 `"archived"`。其余字段（`doc_token`、`wiki_node_token`、
   `source_revisions`、revision 计数）原样保留，作为审计记录。
2. 同步索引中 `海外绘本/小老鼠迈尔斯/content-spec` 的条目：保持不变，
   它是该来源从此唯一的活动归属。
3. 旧物理页（`doc_token = Mff8djjiNoTp0vxg4LqcOPFCnRe`）：不移动、不删除、不改写。
   索引入档后，后续同步流程不会再写入它。它的内容由新页覆盖，历史版本保留在飞书
   revision 历史中。
4. 来源 `DDPRwJVDLiJfmSkdpxqcH6u3nOe` 与索引的映射关系在此操作后收束为：
   一来源 → 一活动页（`content-spec`）。

### 5. 你方 wiki-lint 的 `index_key_scope` 检查

确认口径与我方一致。迁移动作完成后，该检查对真机索引的 ERROR 数应降为 0。

## 二、协议文档自身的清理

你方在第一稿第三节提到的 `99_系统控制台` 三项标题不一致问题，我方确认同样存在，
后续在 `Publisher.initialize()` 侧自修，此处不再重复。

## 三、后续待命

- 你方执行上述迁移后，本轮问题闭环。
- Codex 侧不抢先执行任何远端操作。
- 若你方在迁移过程中遇到任何索引或页面校验异常，按协议第 7 节流程标 `needs_review` 并报告。
