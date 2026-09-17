---
name: wiki-ingest
description: 只读枚举和提取配置的原始飞书知识库，将资料归纳为带来源版本向量的候选 Markdown，并通过机械校验生成任务局部 _manifest.json。本技能不写入目标 Wiki，也不依赖任何共享本地资料库。
applies_to:
  slot: none
  type: infrastructure
---

# Wiki Ingest Skill

## 目标

`wiki-ingest` 从配置的原始飞书知识库读取多人上传的资料，按系列、项目和页面类型归纳为候选 Markdown。它只负责三件事：读取原始资料、生成候选、验证候选。所有远程写入、并发控制、合并和发布由 `feishu-knowledge-store` 完成。

## 硬性边界

1. 原始飞书知识库是只读输入。不得对它执行任何创建、更新、删除、移动或权限操作。
2. 本技能不写入目标 Wiki，也不调用目标 Wiki 的写入命令。
3. 本地运行产物只能放在 `<workspace>/.picturebook-screenwriter/tmp/<run_id>/` 下。它们是任务局部缓存，不是共享状态。
4. 任务结束并报告后必须删除该 `<run_id>` 目录。若下一阶段尚未消费 `_manifest.json`，只在 `feishu-knowledge-store` 明确保存或完成 `prepare` 前不删除。
5. 显示标题不作为主键。候选的唯一键是 `series_id/project_id/page_type`。
6. 所有用户可见输出使用中文。

## 前置条件

运行前必须确认：

1. 已知配置文件路径，且其中的 `source_wiki_url` 指向原始飞书知识库。
2. `lark-cli auth status --json --verify` 返回用户身份认证成功。
3. 当前用户对原始知识库根节点有读取权限。
4. `LARK_CLI_PATH`、PATH 中的 `lark-cli`，或 Windows 兼容路径 `D:\lark-cli\lark-cli.exe` 至少一个可用。

除 `auth status` 外，所有 lark-cli 读写命令使用 `--as user`，以保留实际读取者身份。

## 任务局部布局

每次运行创建唯一 `run_id`。以下路径是本技能允许写入的唯一位置：

```text
.picturebook-screenwriter/tmp/<run_id>/
├── settings.json
├── nodes_snapshot.json
├── delta_plan.json
├── delta_state.json
├── cache/
├── texts/
├── capture/
├── scan/
└── wiki_staging/
    ├── _manifest.json
    ├── common/
    └── <project_id>/
```

`settings.json` 是本次运行的批处理配置，仅包含源 URL 和外链扫描过滤参数；不得写入令牌或个人秘密。`cache/` 与 `delta_state.json` 只服务当前运行，不作为下轮共享基线。

## 执行流程

### 1. 预检并创建运行目录

读取配置，提取 `source_wiki_url`。生成 `run_id` 并创建任务局部目录。然后执行只读预检：

```bash
lark-cli auth status --json --verify
lark-cli wiki +node-get --node-token <source_root_token> --as user --format json
```

认证失败、CLI 不可用、根节点不可读取时，终止运行并报告具体原因。不得尝试创建替代知识库。

### 2. 枚举源节点

根据根节点返回的 `space_id` 递归列出节点树。每个内容节点记录：

- `node_token`
- `obj_token`
- 标题与 URL
- 对象类型和扩展名
- 编辑时间
- 可用的修订号

将结果写入 `nodes_snapshot.json`。文件夹节点只递归，不生成候选。

### 3. 做增量判定并读取内容

如果当前运行目录内有 `delta_state.json`，先用 `check_delta.py` 计算变更计划；否则本次按首次运行处理：

```bash
python plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/check_delta.py \
  --nodes <run_id>/nodes_snapshot.json \
  --state <run_id>/delta_state.json \
  --cache-dir <run_id>/cache \
  --out <run_id>/delta_plan.json
```

仅下载或解析 verdict 为 `first_run`、`new`、`changed` 或 `unknown` 的节点。`unchanged` 节点不允许重新网络拉取。读取规则见 `references/feishu-wiki-extraction.md`。每次读取后校验内容完整性并记录来源 URL、标题、节点 token 和修订号。

`deleted` 节点只进入报告；没有足够证据时标为 `unknown`，不得臆测删除。

### 4. 扫描外链并入候选依据

对本次提取出的全部文本执行零网络机械扫描：

```bash
python plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/scan_external_links.py \
  --texts-dir <run_id>/texts \
  --meta <run_id>/texts_meta.json \
  --snapshot <run_id>/nodes_snapshot.json \
  --settings <run_id>/settings.json \
  --out <run_id>/scan/candidates.json
```

必须保留 `SCAN_DONE` 证据行，并报告内部链接、硬排除、代理层丢弃和超限裁剪的数量。不得静默丢弃 URL。

只有用户明确确认后，才可抓取候选外链。外链失败不阻断宿主候选生成。若执行环境没有网络工具，记录降级原因并保留待确认 URL 清单。

### 5. 归纳候选页面

按照 `references/structured-output-templates.md` 合成候选 Markdown。每个候选必须：

- 按 `series_id`、`project_id`、`page_type` 归属到一个逻辑键。
- 在正文的重要事实旁写中文来源引用。
- 在 `source_node_tokens` 中列出来源节点 token。
- 在 `source_revision_parts` 中按相同顺序列出来源修订号，两个列表必须一一对应。
- 保留 `source_feishu_url` 和 `revision_id` 作为人读审计字段。
- 保留 XLSX Preservation 标记和机器可读 YAML 块。

内容冲突登记到对应项目的 `corrections.md` 候选；执行者不得自行裁决哪一方正确。

### 6. 机械校验并生成清单

运行：

```bash
python plugins/picturebook-screenwriter/skills/wiki-ingest/scripts/generate_entries.py \
  --staging-dir <run_id>/wiki_staging
```

退出码非零时，本次候选不合格，不得交给 `feishu-knowledge-store`。校验包括：

1. 必填 frontmatter 完整。
2. 页面类型合法。
3. 来源节点 token 无重复、无空值。
4. 来源修订列表长度一致且无空值。
5. 台账机器数据块存在。
6. 逻辑键全局唯一，显示标题重复本身不是错误。

校验通过后，`wiki_staging/_manifest.json` 必须同时包含原有 `root/series` 层级结构和确定性的 `entries` 扁平列表。每条 `entries` 记录至少包含：

```json
{
  "path": "worldview.md",
  "key": "海外绘本/小老鼠迈尔斯/worldview",
  "page_type": "worldview",
  "series_id": "海外绘本",
  "project_id": "小老鼠迈尔斯",
  "source_revisions": {
    "node-a": "17",
    "node-b": "28"
  }
}
```

### 7. 移交与清理

将 `wiki_staging/_manifest.json` 和候选目录交给 `feishu-knowledge-store` 做后续 `prepare`、决策验证和 `apply`。移交时明确说明：

- 运行目录位置
- 候选数量
- 逻辑键清单
- 每个候选的来源版本向量
- 外链扫描与降级摘要

`prepare` 完成或用户明确不需要继续后，删除整个 `<run_id>` 目录。删除前报告将删除的根路径；不得删除任何通配路径或工作区其他目录。

## 完成报告

报告必须使用中文并包含：

- 读取的源知识库 URL
- 处理、跳过、失败和待确认的节点数量
- 生成的候选数量与逻辑键清单
- 外链扫描证据行和降级说明
- 机械校验结果
- `_manifest.json` 路径
- 是否已清理任务局部运行目录

## 禁止事项

- 禁止写入原始知识库或目标 Wiki。
- 禁止把本地候选、缓存、状态或清单当作共享权威。
- 禁止向远程索引、锁、冲突队列或任何 Feishu 文档写入数据。
- 禁止省略来源节点和修订向量。
- 禁止用标题规范化替代逻辑键。
- 禁止在任务结束后把运行目录留在 `.picturebook-screenwriter/tmp/` 下。
