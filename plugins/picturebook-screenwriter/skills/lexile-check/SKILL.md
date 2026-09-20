---
name: lexile-check
description: 调用外部 Lexile 服务测量文本难度；只返回实测值，不估算，不硬编码目标区间。
---

# Lexile Check

## Workflow

1. 向用户确认是否执行外部测量，并说明需要网络和 API 凭据。
2. 将完整页文交给 `scripts/lexile_api.py` 指向的实际客户端。
3. 返回 `score`、`band` 和 `measured`。
4. 若 `measured` 为 false，报告“未测量”，不得显示估算值。

## Output

```json
{"score": 320, "band": "Grade 1", "measured": true}
```
