# KB-AI 远端只读验收记录

> 状态：待执行。只读验收不等同于 publish 授权。

## 证据字段

| timestamp | command | control revision | sample key | decision | schema result | unresolved items |
| --- | --- | --- | --- | --- | --- | --- |
| 待填写 | `store_cli.py source-baseline --out <path>` | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |

## 安全边界

- 不得保存令牌、认证信息、私密正文或未脱敏数据。
- 只读命令失败时记录错误和影响范围，不得将结果报告为 published。
- 本记录不能替代用户对真实 publish 的 explicit user authorization。
