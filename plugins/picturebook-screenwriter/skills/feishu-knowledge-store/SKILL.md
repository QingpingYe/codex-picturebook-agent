---
name: feishu-knowledge-store
description: 通过远端租约、docx revision 前提、人工优先三方合并和所有权队列，将 wiki-ingest 候选安全发布到多人共享的飞书权威知识库。
---

# Feishu Knowledge Store

## 目标

将 `wiki-ingest` 生成的候选 Markdown 发布到目标飞书 Wiki。目标 Wiki 是唯一共享权威，人工编辑优先。

## 硬性规则

1. 初始化系统树和同步只能持有远端租约。
2. 每个 docx 页面必须用当前 `revision_id` 更新。
3. 含资源、评论或未知块的页面进入 `needs_review`，不得自动覆盖。
4. `docs +update` 的完整结果必须检查 `warnings`、`partial_success` 和 `result`。
5. 人工新增内容必须保留，人工删除内容不得被恢复。
6. 出现冲突不得静默吞掉，必须登记到冲突队列并报告。

## 使用流程

1. 运行 `preflight`，验证用户身份、原始库读取和目标库读写能力。
2. 首次运行 `initialize`，在远端系统树中创建控制页。
3. 运行 `wiki-ingest` 生成任务局部 `_manifest.json`。
4. 运行 `prepare`，读取历史版本、当前版本和候选版本，并获取远端锁。
5. 为每个 `agent_decision` 生成一个 `MergeDecision`，由 `merge_protocol` 校验。
6. 运行 `apply`，完成条件更新并在结束时释放锁。

## 输出

每次同步后报告：

- 已发布 / 保留 / 入队 / 失败 / 重试的数量
- 涉及的逻辑键和页面标题
- 冲突队列中出现的新记录
- 每个页面更新前后的 `revision_id`

