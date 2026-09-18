---
name: knowledge-loader
description: 只读检索飞书权威知识库，为编剧工作流提供带 revision 和来源向量的证据包；离线时仅在明确允许时使用最后确认的本地缓存。
---

# Knowledge Loader

## 目标

在创作或修改绘本前，从目标飞书 Wiki 读取权威知识，并输出可追溯的证据包。

## 使用规则

1. 远端读取前先运行 `../feishu-knowledge-store/scripts/lark_cli_bootstrap.py`。若结果为 `missing`，先向用户说明将执行官方安装器，获得明确批准后加 `--install` 重跑；若为 `unsupported`，按目标 Wiki 不可用处理。
2. 默认从远端索引和 docx 页面读取，不使用本地 staging；本技能只读，不写飞书。
3. 创作依赖必须使用 `scripts/authority.py` 的 `AuthorityLoader.load()`。它会校验页面系统元数据的 `key` 和 `source_revisions` 与索引一致；缺失必需页时报告“权威知识缺失”。
4. 每次引用必须记录 `key`、`doc_token`、`revision_id` 和 `source_revisions`。用 `scripts/dependencies.py` 的 `build_dependency_record()` 生成锁定记录，`render_dependency_record()` 渲染 `built_against`。
5. 落盘前先用 `scripts/collision.py` 的 `check_collisions()` 扫描草稿与证据中的共有术语；冲突只提示，不自动改写。
6. 重读旧产物时，用 `parse_dependency_record()` 从 Markdown 末尾提取 `built_against`，再用 `AuthorityLoader` 重新读取当前页面，最后调用 `find_stale_dependencies(record, current_index, current_bundle)`。
7. `find_stale_dependencies()` 的第三个参数应传当前读取的 `KnowledgeEvidenceBundle`。只传索引时结果为 `index_unverified`，不得宣称产物仍为最新。
8. `needs_review` 条目仍可读取，但必须警告存在待处理冲突；`archived` 条目必须按陈旧处理。
9. 目标 Wiki 不可用时，只有调用方显式允许，才能使用最后确认的本地缓存。缓存不是权威版本，必须在警告中说明“离线”和“最后确认”。

## 输出

返回 `KnowledgeEvidenceBundle`：

- `items`：命中的知识页
- `warnings`：中文警告
- `offline`：是否来自缓存
- `fetched_at`：读取时间

## 依赖记录

`built_against` 是本地产物元数据，只允许追加到已获用户批准的 Markdown 产物正文末尾，并且必须是文件最后一个章节。格式由 `render_dependency_record()` 生成，后续用 `parse_dependency_record()` 提取并重新校验。

允许的 `artifact_type` 为：`positioning`、`topic_plan`、`worldview`、`characters`、`outline`、`script`。

