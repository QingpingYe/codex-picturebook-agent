---
name: feishu-knowledge-store
description: 通过远端租约、docx revision 前提、正文内容比较和写后回读，将 wiki-ingest 候选安全发布到多人共享的飞书权威知识库。
---

# Feishu Knowledge Store

## 目标

将 `wiki-ingest` 生成的候选 Markdown 发布到目标飞书 Wiki。目标 Wiki 是唯一共享权威；候选正文是页面内容的权威来源，系统元数据和来源向量只记录来源归属与 revision 基线。

## 硬性规则

1. Resolve configuration in this order: explicit `--config`, `PICTUREBOOK_KB_CONFIG`, nearest workspace ancestor, platform user directories, then deprecated `~/.picturebook-screenwriter`. Plugin cache is not durable configuration. Target supports `root_mode: "space"` for root-level system containers or `"node"` with an explicit `root_token`.
2. 初始化系统树和同步只能持有远端租约。
3. 每个 docx 页面必须用当前 `revision_id` 更新，并在写后回读正文、页尾和 revision；更新响应的 revision 不能直接当作最终状态。
4. 含资源、评论或未知块的页面进入 `needs_review`，不得自动覆盖。
5. `docs +update` 的完整结果必须检查 `warnings`、`partial_success` 和 `result`。
6. 是否发布只由正文比较决定：比较前对称忽略纯排版标记；改词、增删文字和改标点必须发布。来源版本向量相同不代表内容未变，不得据此跳过比较。
7. 覆盖曾是人工编辑的目标页时必须在报告中披露（`overwritten_human_edits`）。远端若仍有 `AI_KB_CONFLICT_QUEUE_V1` 页，只保持原位：不读取、不追加、不删除、不因缺失而创建。
8. 无法安全发布时标记 `needs_review`，下一轮自然重试；不得因此去读取冲突队列，`archived` 条目不得由同步自动恢复。

## 使用流程

1. Run `store_cli.py config-status` to confirm durable configuration and CLI availability before remote work.
2. 运行 `scripts/lark_cli_bootstrap.py` 检查兼容的 lark-cli。若结果为 `missing`，先向用户说明将执行 `npx @larksuite/cli@latest install`，获得明确批准后加 `--install` 重跑；若为 `unsupported`，不得替换用户已有 CLI。
3. 运行 `preflight`，验证用户身份、原始库读取和目标库读写能力。安装 CLI 不代表完成登录；认证失败时由用户本人执行 `lark-cli auth login`。
4. 控制页位于远端系统树中：`AI_KB_INDEX_V1` 是唯一同步状态权威，`AI_KB_LOCK_V1` 提供租约；只读准入策略来自 `AI_KB_SOURCE_ADMISSION_V1`：未裁定默认纳入，只有 `exclude` 生效并作用于自身、全部后代和后代容器；页缺失表示空排除，损坏则硬失败。远端命令在 `build_components()` 中通过 `Publisher.resolve_control_plane()` 解析索引和锁，必须保留准入页的只读边界，不得创建替代文档；`Publisher.initialize()` 是发布流程为新建页定位（必要时创建）系统树的路径，不用于在控制页缺失时顶替。
5. 在 `wiki-ingest` 建立本轮运行目录后，先运行只读 `source-baseline --out <run-id>/source_baseline.json`，再运行 `wiki-ingest` 生成任务局部 `_manifest.json`；该投影包含远端索引和准入排除快照，不取锁、不写远端。
6. 运行 `prepare`，读取原始库节点并校验 `wiki-ingest` 生成的候选清单；它只写任务局部 run 目录，不接触目标库，也不持有锁。
7. 运行 `publish`：先取得远端锁，再逐页读取当前目标页并比较候选正文与当前正文；确有内容变化才做条件更新、写后回读和索引回写，内容相同则只刷新索引时间。
8. 运行 `verify`，核对本轮报告与远端状态。

## 输出

每次同步后报告：

- 已发布 / 保留 / 失败 / 重试的数量
- 涉及的逻辑键和页面标题
- 被覆盖的人工编辑逻辑键（`overwritten_human_edits`）
- 每个页面更新前后的 `revision_id`，以及 `page_overwritten` / `index_committed`
