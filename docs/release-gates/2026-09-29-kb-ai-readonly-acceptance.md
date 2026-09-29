# KB-AI 远端只读验收记录

> 状态：Codex 侧只读验收通过；不构成真实 publish 或并发放行授权。

## 证据

| timestamp | command | control revision | sample key | decision | schema result | unresolved items |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-09-29 Asia/Shanghai | `store_cli.py resolve` | index=24, lock=89, admission=5 | `海外绘本/小老鼠迈尔斯/worldview` | 12/12 页面可读取；9 个 admit、0 个 exclude | 控制页、索引、页尾和契约 lint 均通过 | 远端没有 exclude 条目；远端没有 legacy 缺失时间条目；未执行 WorkBuddy 运行时复跑 |

## 只读检查结果

- `AI_KB_INDEX_V1`：索引 revision 24，12 条记录，`source_edit_times` 覆盖率 12/12，legacy 缺失 0 条。
- `AI_KB_LOCK_V1`：revision 89；`run_id`、`holder`、`started_at`、`expires_at` 均为空，未发现活动租约。
- `AI_KB_SOURCE_ADMISSION_V1`：revision 5；schema v1，六字段完整，9 条 `admit`，0 条 `exclude`。
- 页面 fixture：12 页与索引逻辑键、来源 revision、来源时间和 `last_ai_revision_id` 全部匹配。
- Markdown 往返：12/12 页正文比较稳定，0 个资源/评论/未知块页面。
- `contract_lint.py --fixture`：0 errors、0 warnings。
- 只读命令未调用 `publish`、`docs update`、`docs create`、lock acquire/release。

## WorkBuddy/Codex 跨侧比对

- 准入六字段一致：`token,title,decision,decided_by,decided_at,reason`。
- 未裁定默认纳入、仅 `exclude` 生效、自身和全部后代/容器纳入排除闭包的语义一致。
- `source_edit_times` 作为远端来源基线的语义一致。
- 写入后回读 revision，`last_seen_revision_id` 取回读值的纪律一致。
- 未在双方实现中发现当前协议字段名称或失败语义分歧。

## 未覆盖项

- 远端准入页当前没有 `exclude` 条目，真实远端子树排除未直接触发；由离线测试覆盖。
- 远端索引当前没有缺失 `source_edit_times` 的 legacy 条目，真实降级路径未直接触发；由离线测试覆盖。
- 未执行真实 publish，也未解除 Codex/WorkBuddy 并发禁令。
- 未独立复跑 WorkBuddy 运行时验收；本次跨侧比对基于其交接单、实现与测试源码的静态证据。

## 安全边界

- 不得保存令牌、认证信息、私密正文或未脱敏数据。
- 只读命令失败时记录错误和影响范围，不得将结果报告为 published。
- 本记录不能替代用户对真实 publish 的 explicit user authorization。
