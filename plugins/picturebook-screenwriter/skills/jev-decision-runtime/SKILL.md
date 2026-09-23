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

## 路由

运行器按策略文件里的规则为**每个 item**（知识块、页面、红线）计算一条路由，而不是按问题计算——一条路由可以由多个答案共同决定。规则按声明顺序首命中：

- band 由 `routing.bands` 的 `clear_at_or_below` / `risk_at_or_above` 决定，用 Decimal 比较。
- 只有 `noul` 答案可以进 band；`choice` 与 `score` 的数值语义不同，强行 band 会让规则表达出作者没写的含义。
- 缺少某个答案的条件一律不成立，item 落入 `fallback_route`。
- 没有路由的结果只可能来自失败或中断，绝不代表“已通过”。

## 安全约束

- 凭证只从环境变量 `TYPESAFE_API_KEY` 读取，缺失或全空白即视为缺失。
- endpoint 固定为 `https://api.typesafe.ai/v1/systemone`，不接受 base URL 覆盖，禁用 redirect。
- 密钥绝不写入配置、manifest、trace、错误信息或命令行参数。
- 为了在缺 key 之后原地续跑，运行器把本次请求体写入本地 run 目录的 `request.json`；这是运行目录里**唯一**可以携带原文的文件，它不含密钥、不进插件包、也不进导出物，其余落盘物只保留 hash、计数、引用 ID 与概率。

## 执行

```bash
python scripts/jev_runner.py run \
    --request <pb-jev-request-v1.json> \
    --run-dir <run_dir> \
    [--policy <decision-policies.json>]

python scripts/jev_runner.py resume \
    --run-dir <run_dir> \
    --input-refs <当前 revision 向量.json> \
    [--policy <decision-policies.json>]
```

- 两个子命令都把 `pb-jev-result-v1` 打到 stdout，成功或进入等待状态返回 0，失败返回 1，用法错误返回 2。
- 凭证只从环境变量 `TYPESAFE_API_KEY` 读取。任何 `--api-key` / `--token` / `--secret` 形式的参数都会被**在解析前**拒绝，且错误信息只回显参数名、不回显参数值。
- `--policy` 省略时使用 `references/decision-policies.json`。
- `resume` 需要调用方提供**当前** revision 向量；与 pending call 中记录的不同即判定 `superseded`，不会改用新输入重跑。
- 只有当磁盘上的 `result.json` 记录了它回答的正是本次请求（`trace.input_sha256` 等于本次请求的哈希）时，`resume` 才会直接返回它：`request.json` 每次派发都会被覆盖，而 `result.json` 只由成功的派发写入，所以目录里可能留着更早一次请求的结论。记录缺少该字段、或属于另一次请求时，`resume` 按 pending 记录继续（等待 key 的批次可以重发，去向不明的批次仍只回报 `outcome_unknown`），并且**不会**删掉那条 pending 记录，因为它可能是“这次调用已经计费”的唯一凭据。

## 强制力分级

本技能的强制力边界见 `docs/ENFORCEMENT.md`：

- `runtime_required`：无 key 不构造请求；endpoint 必须命中 allowlist；redirect 一律拒绝；响应的 `model` 必须等于策略里的 `pinned_model`；租约互斥；终态结果先原子落盘再清除 pending call。
- `script_checked`：pending call 的 fingerprint 与 revision freshness；成本与 token 口径。
- `prompt_only`：是否使用本技能、是否在缺 key 时暂停等待而不是自动回退普通 LLM。
