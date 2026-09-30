# KB-AI 发布门

> 状态：全部发布门已满足；Codex 与 WorkBuddy 并发操作同一 KB-AI 已于 2026-09-30 获用户明确授权。

## 离线条件

- [x] 第一批、第二批、第三批实现和测试完成。
- [x] Feishu store、wiki-ingest、knowledge-loader 和契约测试全部通过。
- [x] 静态旧口径扫描没有未解释的运行时引用。

## 远端只读条件

- [x] read-only acceptance 记录完成并通过。
- [x] Codex 与 WorkBuddy 对控制页、来源时间、准入和报告字段的静态证据一致。
- [x] 准入缺失、准入损坏、排除子树和来源时间缺失均按 fail-closed 验证；远端未触发的分支由离线测试覆盖。

## 发布条件

- [x] explicit user authorization 已记录到本文件：授权一次真实 publish。
- [x] publish run owner、预期 revision、停止条件和回滚/修复路径已记录。
- [x] 已于 2026-09-30 获 explicit concurrency authorization：允许 Codex 与 WorkBuddy 并发操作同一 KB-AI。

## 离线与远端证据

- [x] Feishu store：222 项通过。
- [x] wiki-ingest：92 项通过。
- [x] knowledge-loader：38 项通过。
- [x] KB-AI 契约与文档一致性：25 项通过。
- [x] picturebook-screenwriter 插件级测试：188 项通过，1 项跳过。
- [x] 远端只读：index revision 24；lock revision 89 空闲；admission revision 5；12/12 页面通过。
- [x] `source_baseline.py` 无 update/create/lock 写调用。
- [x] explicit user authorization：2026-09-30 授权一次真实 publish，并授权 Codex/WorkBuddy 并发操作同一 KB-AI。

## 发布决定

- 决定：本轮不执行真实 publish，不解除 Codex/WorkBuddy 并发禁令。
- 原因：用户授权的是远端只读验收；读取授权不能推导写入或并发授权。
- publish run owner、目标 revision 和回滚路径：N/A，因本轮没有发布动作。
- 后续若获明确发布授权，需先重新读取 index/lock/admission revision，再按当前 revision 执行。

## 2026-09-30 真实 Publish 记录

- 授权范围：一次真实 publish。
- run owner：Codex 当前会话。
- canary key：`海外绘本/小老鼠迈尔斯/worldview`。
- 发布前：index revision 24；lock revision 89 空闲；页面 revision 8。
- 发布结果：candidates=1，preserved=1，published=0，failed=0，retried=0。
- action=`preserve`，reason=`content unchanged`，页面 revision 保持 8。
- index_committed=true，overwritten_human_edits=[]。
- 发布后：lock revision 93 且空闲；index revision 26。
- 停止条件：revision 冲突超过重试、页面出现资源/评论/未知块、写后回读不一致、索引回读不一致。
- 回滚/修复路径：若页面被意外覆盖，使用写后回读 revision 作为身份基线，停止并发并按 retained report 逐页修复；本轮未发生页面覆盖。

## 2026-09-30 并发放行记录

- 授权范围：Codex 与 WorkBuddy 可并发操作同一 KB-AI。
- 强制共同纪律：每次写入必须持有 `AI_KB_LOCK_V1` 租约，使用当前页面 revision，写后回读，索引提交后回读。
- revision 冲突：双方都必须重读当前页面和索引后重新判定；不得沿用旧 revision 盲目重试。
- 准入纪律：双方都必须读取 `AI_KB_SOURCE_ADMISSION_V1`；`exclude` 子树不得写入。
- 来源基线：双方都以远端索引的 `source_edit_times` 为增量判据，不读取跨轮本地 state。
- 停止条件：锁争用无法安全取得、revision 重试耗尽、资源/评论/未知块、准入 schema 损坏、页级回读不一致（页 `needs_review`）或控制面回读不一致（停止本轮）。
- 回滚/修复：触发停止条件的一方不得继续写页；保留锁到安全释放，保留本地 sync report，重新读取远端页面和索引后由双方按逻辑键对账修复。
- 当前实现状态：Codex 侧所有发布门已满足；WorkBuddy 侧运行证据未由本会话执行，但其静态契约与失败语义已完成比对。
