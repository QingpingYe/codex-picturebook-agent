---
name: jev-decision-runtime
description: 共享 Jev 决策运行器。仅当用户已选择 Jev 辅助路径时使用；负责构造 TypeSafe SystemOne 请求、读取本机凭证、校验响应契约、处理重试与凭证等待、维护恢复点和执行租约、记录耗时与成本。它只做原子概率判断，不生成故事、不解释结论、不写文件、不调用图片生成。
---

# Jev Decision Runtime

本技能是插件内部的共享运行器，不直接面向用户。入口技能、`knowledge-loader`、`pre_output-baseline` 与 `quality-baseline` 都通过它调用 Jev，不各自实现 HTTP、重试或计费。

## 定位

- 只处理第 2 类工作：答案空间明确、需要自然语言常识的原子小判断。
- 不生成文本，不生成图片，不做计数、算术、日期比较或多层间接推理——这些留在确定型脚本。
- 普通 LLM 继续负责生成、解释性终审和修改建议。

## 能力边界（不得夸大）

1. 全局 `typesafe-ai` skill 只是给模型看的设计指南，它没有 transport，不能发起请求。真正的调用由本技能完成。
2. 本技能不能证明用户意图：它只能保证“没有 key 就不发请求”“endpoint 不是 allowlist 就拒绝”“响应不完整就不判定通过”。是否被调用仍取决于技能指令。
3. 本技能不拥有写工作区文件、同步飞书、调用图片生成或修改 DAG 状态的权限。

## 安全约束

- 凭证只从环境变量 `TYPESAFE_API_KEY` 读取，缺失或全空白即视为缺失。
- endpoint 固定为 `https://api.typesafe.ai/v1/systemone`，不接受 base URL 覆盖，禁用 redirect。
- 密钥绝不写入配置、manifest、trace、错误信息或命令行参数。
- 为了在缺 key 之后原地续跑，运行器把本次请求体写入本地 run 目录的 `request.json`；这是运行目录里**唯一**可以携带原文的文件，它不含密钥、不进插件包、也不进导出物，其余落盘物只保留 hash、计数、引用 ID 与概率。
