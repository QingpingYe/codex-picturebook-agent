---
name: knowledge-loader
description: 只读检索飞书权威知识库，为编剧工作流提供带 revision 和来源向量的证据包；离线时仅在明确允许时使用最后确认的本地缓存。
---

# Knowledge Loader

## 目标

在创作或修改绘本前，从目标飞书 Wiki 读取权威知识，并输出可追溯的证据包。

## 使用规则

1. 默认从远端索引和 docx 页面读取，不使用本地 staging。
2. 每次引用必须记录 `key`、`doc_token` 和 `revision_id`。
3. `needs_review` 条目仍可读取，但必须警告存在待处理冲突。
4. 目标 Wiki 不可用时，只有调用方显式允许，才能使用最后确认的本地缓存。
5. 缓存不是权威版本，必须在警告中说明“离线”和“最后确认”。

## 输出

返回 `KnowledgeEvidenceBundle`：

- `items`：命中的知识页
- `warnings`：中文警告
- `offline`：是否来自缓存
- `fetched_at`：读取时间

