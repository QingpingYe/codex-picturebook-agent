# 两条路径的比较步骤

同一份 `benchmark_case_id` 分别跑一次普通 LLM 与一次 Jev 辅助，才能并排比较。

## 1. 让两次运行共享同一个 case identity

`case identity` 的六个字段必须逐字一致：

| 字段 | 来源 |
| --- | --- |
| `benchmark_case_id` | Jev 请求里的同名字段（`telemetry.benchmark_case_id()` 产出） |
| `input_revision_vector_sha256` | 权威知识版本向量的 sha256 |
| `draft_sha256` | 被审草稿的 sha256 |
| `policy_version` | `decision-policies.json` 里该 operation 的 `policy_version` |
| `rule_version` | 该 operation 的规则版本 |
| `artifact_params` | `age_band`、`genre`、`page_count` |

任一不一致，报告会标 `comparable: false` 并列出原因。**不得把不可比较的数字当成结论。**

运行目录只能核验其中两项：trace 自己带着 `benchmark_case_id`，而 `policy_version` 必须
等于本次真正跑过的某个 operation 的策略版本。其余四项在磁盘上没有第二份副本，报告把它
们标为「仅凭人工声明」（`identity_attestation.declared_only`），不会假装它们被核验过。

## 2. 从 CC Switch 手工读出 LLM 侧用量

本插件不读 CC Switch 的数据库，也不依赖它的存储结构。请在 CC Switch 里按下面的顺序筛出这次运行，然后手抄成 JSON：

1. 用本次 trace 的 `started_at` / `finished_at` 作为时间窗过滤请求。
2. 只看发起本次运行的那个 Codex 会话与模型。
3. 汇总 `model`、`input_tokens`、`output_tokens`、`cache_tokens`、请求数与估算成本。

写成：

```json
{
  "identity": { "...": "与第 1 步完全一致" },
  "model_label": "gpt-5-codex",
  "source": "cc-switch-manual-entry",
  "elapsed_ms": 180000,
  "request_count": 12,
  "input_tokens": 54000,
  "output_tokens": 9000,
  "cache_tokens": 12000,
  "estimated_cost_usd": "1.234500000000",
  "knowledge_items_entered": 180,
  "quality_items_entered": 160,
  "issues_found": 7,
  "misses_or_disagreements": 1,
  "upgraded_llm_usage": {
    "elapsed_ms": 42000,
    "request_count": 3,
    "input_tokens": 12000,
    "output_tokens": 900,
    "cache_tokens": 0,
    "estimated_cost_usd": "0.210000000000"
  }
}
```

上面这份汇总描述的是**普通 LLM 那一列**（整轮跑完的普通 LLM 路径）。

`upgraded_llm_usage` 是**可选的第二个块**，也是 spec §10.3 留给 Jev 辅助列的那一格：预筛把升级项交回普通 LLM 再跑一遍，那批调用有自己的请求数、token、耗时和账单。按同一套筛法，用**升级项复核那一段**（在预筛 trace 的 `finished_at` 之后）的窗口把它的汇总填进来。

不填这个块，报告不会拿预筛自己的成本冒充该路径的总账：Jev 列的 `llm_*` 与 `total_*` 一律读作「未测得」，并在 `notes` 里写明。块里少写一半（例如没给 `estimated_cost_usd`）同样让总账保持未知——只测到一半的账单加起来不是这条路径的账单。

块里的键名必须拼对：出现不认识的键（例如 `estimatd_cost_usd`）会被**拒绝**，而不是当成「没测过」——否则你亲手写下的那个数字会静默地不进报告。

`source` 必须是 `cc-switch-manual-entry` 或 `manual`；写下任何像数据库路径的值都会被拒绝，那意味着这个插件开始读它承诺不碰的私有存储。

`estimated_cost_usd` 必须写成带引号的字符串。写成 JSON 数字（`1.2345`）会被拒绝：JSON
数字按二进制浮点解析，账单口径不能靠浮点凑。这条规则对 `upgraded_llm_usage.estimated_cost_usd` 同样生效。

## 3. 生成报告

```bash
python scripts/compare_cli.py \
    --run-dir <jev_assisted 的 run 目录> \
    --llm-usage <上一步的 JSON> \
    [--policy <decision-policies.json>] \
    [--output-dir <绝对导出目录> --plugin-root <插件根>]
```

不带 `--output-dir` 时只把 JSON 打到 stdout，不写任何文件。带 `--output-dir` 时目录必须是绝对路径且不在插件目录内，报告会写进 `<output-dir>/path-comparison-audit/`；此时 `--plugin-root` 是必需参数，缺了会报错而不是退化成"写到随便哪"。

## 4. 怎么读报告

报告里的两列**不是同一个测量装置**，读之前先认清每一列的数字从哪来：

| 指标 | 普通 LLM 列 | Jev 辅助列 |
| --- | --- | --- |
| `elapsed_ms`、`request_count` | 上一步手工汇总 | 运行目录里的 trace |
| `input_tokens`、`output_tokens` | 上一步手工汇总 | trace 的 `usage` |
| `cache_tokens` | 上一步手工汇总 | 恒为 `null`：trace 尚未记录缓存 token，这一格读作"没有测过"，不是"Jev 省了缓存" |
| `estimated_cost_usd` | 上一步手工汇总 | trace 的 `estimated_cost_usd` 求和 |
| `knowledge_items_entered` | 上一步手工汇总：本次全量进入的知识块 | **只**取 `knowledge_relevance` 自己的 `escalated_count` 之和，即该 operation 交给普通 LLM 继续处理的块数 |
| `quality_items_entered` | 上一步手工汇总：本次全量复核的页面维度 | **只**取 `text_quality_prefilter` 自己的 `escalated_count` 之和，即升级项 |
| `issues_found` | 上一步手工汇总 | 升级项 + 整批失败数，即需要普通 LLM 再看一遍的项；**不含**代理冲突项（见下） |
| `misses_or_disagreements` | 上一步手工汇总 | 预筛本身给不出，恒为 `null` |
| **升级项回流普通 LLM 的请求数 / token / 成本** | 上一步手工汇总（本列本身即全量普通 LLM） | 上一步的 `upgraded_llm_usage` 块（`llm_request_count`、`llm_input_tokens`、`llm_output_tokens`、`llm_cache_tokens`、`llm_elapsed_ms`、`llm_estimated_cost_usd`）；没提供就读作「未测得」 |
| **本路径估算总成本**（`total_estimated_cost_usd`） | 上一步手工汇总的 `estimated_cost_usd` | 预筛 estimate + `upgraded_llm_usage.estimated_cost_usd`；任一半未测得即为「未测得」，既不是 `0`，也不会退化成预筛那一半 |
| **本路径估算总耗时**（`total_elapsed_ms`） | 上一步手工汇总的 `elapsed_ms` | 预筛耗时 + `upgraded_llm_usage.elapsed_ms`，口径同上 |

两行的口径不同，所以它们各自取自己 operation 的数字：知识块和页面维度不是同一种
单位，一个 operation 没跑就留空，不会拿另一个的数字顶上。

由此：

- `screened_clear` 与 `escalated` 来自 Jev 侧 trace；`runtime_failure` 是未能完成的项数。
- trace 里的 `screened_clear_count` / `escalated_count` 由该 operation 在派发时通过共享
  runner 的 `verdicts=` 钩子写入：两个数相加等于本次真正判定过的项数。
- **升级包的大小不等于 `escalated_count`**：升级包 = 判定派生的升级项 + 整批失败项 +
  代理冲突项，即 `escalated + runtime_failure + summary.escalated_by_proxy_conflict`
  （后两项见同一次运行的 `--report-out`）。字面命中却被模型判 clear 的冲突项本来就是
  `screened_clear`，所以它在 trace 的两个计数里都不出现。因此报告里的 `issues_found`
  行只算了「升级项 + 整批失败数」，**不含**代理冲突项；要拿准确口径请读那份 `summary`。
- 报告会逐 operation 列出 `jev_operations`（`item_count`、`screened_clear_count`、
  `escalated_count`、`runtime_failure`、`traces_without_verdicts`、耗时与成本）。这里的
  `item_count` 是请求引用到的权威页数，**不是**进入 LLM 的项数，别拿它当上面那两行。
- **用 `jev_runner resume` 从凭据等待继续的批次不带判定计数**：CLI 的 `resume` 没有该
  operation 的路由可以交给 runner，于是那条已经成功、却把两个计数都写成 0 的 trace 会被
  记成 `traces_without_verdicts`（`notes` 里也会出现同名条目）。它旁边的两个计数都只统计了记下来的批次，
  升级率**可能偏高也可能偏低**，
  所以只要存在这类 trace，该 operation 的建议一律是 `keep_experimental` 并写明原因，不会给出
  `eligible_for_calibrated`，也不会借它条的数字给出 `review_thresholds`。要拿可比较的升级率，
  请整条 run 重跑一次，而不是靠 `resume` 补记。
- 报告开头会写 `trace 数` 与其中属于其它 case 的条数；运行目录里出现多次运行
  （trace 的 `run_id` 不止一个）时直接判 `comparable: false` 并列出 run_id，不把两次
  运行的数字相加后当成一次。
- **升级率过高说明阈值太严**（`review_thresholds`），而不是"Jev 不行"。
- **`fallback_used: true` 的那次运行不得计入性能比较**——它是用户显式切换路径，不是同一条路径的样本。
- 缺 `usage` 的 trace 其 token 与成本为 `null`，报告不会用字符数替它编一个数。
- Jev 侧的 `notes` 会写出这次真正跑过的 operation；两边跑的 operation 不对等时，耗时与成本不可直接相减。
- 别把两次运行的 token 或成本直接相加：两侧口径不同，报告只做并排。

## 5. 转 calibrated 的条件

报告只给建议（`keep_experimental` / `review_thresholds` / `eligible_for_calibrated`），**不会自己改状态**。要转 `calibrated` 必须同时满足：

1. 测量用的是同一 case identity，报告 `comparable: true`。
2. 本次运行没有 `runtime_failure`。
3. 本次运行没有 `traces_without_verdicts`（即没有被 `resume` 补记、因而缺计数的批次）。
4. 按 `references/calibration-samples.md` 用 `calibrate.py` 选出了满足假阴性预算的阈值。
5. 人工复核升级项与漏检，确认可接受。

改的是 `decision-policies.json` 里该 operation 的 `calibration_status` 与 band。不同 operation 不共享阈值。

报告里的校准建议是**逐 operation**的，用的是该 operation 自己的升级率：本次没有测量的
operation 不会给出建议（"没测到"不是任何方向的证据），所以一次只跑了页面预筛的运行
不会替知识筛选下结论。

第 3 步的输入 `outcomes.json`（逐样本的 `sample_id` 与 `probability`）**目前仍要人工产出**：
本阶段没有提供生成它的命令。可行的做法是按 `calibration-samples.md` 逐样本跑一次该
operation，把每个样本的 `probability` 与是否失败抄成一个 JSON 数组；`calibrate.py` 只认
这个数组，不会去运行目录里替你找概率。这一步在自动化之前，`eligible_for_calibrated` 只能
当作"值得人工试算"的信号，不能当作已完成的校准。
