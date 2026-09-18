# 飞书知识库只读提取协议

## 适用边界

本协议只服务 `wiki-ingest` 的原始资料提取阶段。目标是把配置的飞书知识库读完整、读可审计，并保留机器可解析的来源信息。

必须遵守：

1. 源知识库只读；不执行任何写操作。
2. 输出只进入当前运行目录，不进入共享权威状态。
3. lark-cli 以 `--as user` 运行，保留实际用户身份。
4. 飞书文档不得用普通网页抓取替代 `docs +fetch`；网页通道只能用于用户确认后的非飞书外链。
5. 提取失败要报告，不得静默截断或用部分内容假装完整。

## CLI 与认证

先运行兼容检查：

```bash
python <plugin>/skills/feishu-knowledge-store/scripts/lark_cli_bootstrap.py
```

脚本按以下顺序解析 lark-cli：

1. `LARK_CLI_PATH`
2. PATH 中的 `lark-cli`
3. `%APPDATA%\npm` 中的 `lark-cli.cmd` 及包内二进制
4. `%ProgramFiles%\nodejs\lark-cli.cmd`
5. `C:\lark-cli\lark-cli.exe`
6. `D:\lark-cli\lark-cli.exe`（仅本机兼容回退）

若返回 `missing`，先向用户说明安装来源和影响，获得明确批准后运行：

```bash
python <plugin>/skills/feishu-knowledge-store/scripts/lark_cli_bootstrap.py --install
```

安装使用官方命令 `npx @larksuite/cli@latest install`，要求本机有 Node.js 16+。若返回 `unsupported`，终止并报告版本和路径，不自动替换已有 CLI。

找到兼容候选后先运行：

```bash
lark-cli auth status --json --verify
```

认证失败时提示用户本人运行 `lark-cli auth login`，本次提取终止。安装 CLI 不会自动完成认证。不要保存、输出或转发令牌。

配置中的 `source_wiki_url` 是输入。提取根节点 token 时清理 URL 查询参数和 hash 片段；节点 token 是 URL 中 `/wiki/` 后的段。

## 节点枚举

1. 用 `wiki +node-get --node-token <source_root_token>` 读取根节点，得到 `space_id`。
2. 用 `wiki +node-list --space-id <space_id>` 分页递归列出节点。
3. 每个节点记录 `node_token`、`obj_token`、标题、URL、对象类型、扩展名、编辑时间和可用修订号。
4. 文件夹节点只递归，不进入候选。
5. 节点数超过 100 时分批处理并报告进度。

将节点元数据写入：

```text
.picturebook-screenwriter/tmp/<run_id>/nodes_snapshot.json
```

节点 token 缺失、对象类型不可识别或元数据格式错误时标为 `unknown`，继续用只读方式复核；不得因解析失败直接删除状态。

## 增量判定

当前运行目录中存在 `delta_state.json` 时，先运行 `check_delta.py`。缓存文件和状态都只允许位于当前运行目录：

```text
.picturebook-screenwriter/tmp/<run_id>/cache/
.picturebook-screenwriter/tmp/<run_id>/delta_state.json
```

处理规则：

- `first_run`、`new`、`changed`、`unknown` 必须读取内容。
- `unchanged` 只能使用当前运行缓存，不得重新网络读取。
- `deleted` 必须再次只读复核；无权限或不确定时改回 `unknown`。
- 缓存哈希不匹配一律按 `changed` 处理。

没有跨运行的共享增量权威。上一轮运行目录不应被当作本轮默认基线。

## 文件类型路由

### 原生 docx / Wiki

使用：

```bash
lark-cli docs +fetch \
  --doc <doc_url_or_token> \
  --doc-format markdown \
  --as user \
  --format json
```

不得携带 `--scope` 参数。解析返回 JSON 后记录：

- `content`
- `title`
- `document_id`
- `revision_id`

`revision_id` 必须写入候选的来源版本向量。若 API 未返回修订号，停止生成该候选并报告，不得用占位符替代。

### 上传的 Markdown 文件

先下载到运行目录：

```bash
lark-cli drive +download \
  --file-token <obj_token> \
  --output .picturebook-screenwriter/tmp/<run_id>/cache/<node_token>.md \
  --as user
```

再以 UTF-8 读取。下载失败时该节点进入失败报告。

### 上传的 Word 文档

下载到当前运行目录，用标准库或可用工具解析正文。解析必须覆盖正文、表格、脚注、页眉页脚中的文字，并还原以下三种超链接：

1. OOXML relationship 中的外部链接
2. `fldSimple` 中的 `HYPERLINK`
3. 被拆分到多个 run 的字段码超链接

输出为内联 Markdown 链接。未还原 URL 的 Word 文档不得直接进入候选。

### 上传的 XLSX

下载后转换为 Markdown 表格，并保留每个 sheet、每一行和每一列。输出必须包裹：

```text
<!-- BEGIN_XLSX_PRESERVATION: source="{filename}.xlsx" rows="{rows}" cols="{cols}" sheets="{sheets}" -->
...
<!-- END_XLSX_PRESERVATION -->
```

`rows`、`cols`、`sheets` 必须从实际输出统计。不得概括、排序、合并单元格或改写值。每个来源表旁写：

```text
> 结构化数据来源：{飞书文档标题}（{filename}.xlsx，Sheet “{sheet_name}”）
```

### 其他对象

`sheet`、`bitable`、PDF、PPT、图片或未知对象不自动转写。在报告中记录对象类型、标题、URL 和“需人工处理”，不得伪造候选内容。

## 内容完整性

每次提取后检查：

1. Markdown 非空。
2. 标题、节点 token、修订号齐全。
3. 内容没有明显截断标记或不完整句子。
4. 标题提示多章节时，正文有对应章节结构。
5. Word/XLSX 输出保留了要求的链接和表格数据。

疑似不完整时标记 `PARTIAL`，报告原因。只有完整内容才能进入候选合成。

## 外链扫描

将提取后的正文按节点 token 写入：

```text
.picturebook-screenwriter/tmp/<run_id>/texts/<node_token>.md
```

再运行 `scan_external_links.py`。机械层扫描 Markdown 链接、尖括号链接和裸 URL，并执行归一化、内部链接识别、硬排除、代理打分和上限裁剪。

报告必须包含：

```text
SCAN_DONE: {raw} raw → {unique} unique → {candidates} candidates → {feishu} feishu + {web} web
```

还必须列出：

- 源知识库内部链接转交叉引用的数量
- 硬排除数量和原因
- 代理层丢弃数量和原因
- 超限未抓取数量
- 待用户确认的清单

零 URL 时报告“已验证零 URL”。

非飞书外链只有在用户明确确认后才能抓取。抓取时保留原链接、抓取时间和摘要，并将其作为宿主候选的补充来源。外链失败不阻塞宿主候选。

## 候选来源字段

每个候选 frontmatter 必须包含：

```yaml
source_feishu_url: "{source-url}"
source_node_tokens:
  - "node-token-a"
source_revision_parts:
  - "42"
```

规则：

- `source_node_tokens` 与 `source_revision_parts` 长度和顺序一致。
- token 不得重复、不得为空。
- 修订号不得为空，不得使用 `N/A`。
- 正文中的事实声明必须写中文来源引用。
- 冲突内容写入 `corrections.md` 候选，不得自行裁决。

## 失败处理

| 故障 | 处理 |
| --- | --- |
| CLI 不存在 | 先经用户批准运行官方安装器；仍失败时终止，并列出尝试过的候选路径 |
| CLI 版本不兼容 | 终止，报告版本和路径，不自动替换 |
| 认证失效 | 终止，提示用户重新登录 |
| 根节点无权限 | 终止，不创建替代知识库 |
| 单节点无权限 | 记录失败，继续其他独立节点 |
| 内容不完整 | 标记 `PARTIAL`，不得生成候选 |
| 修订号缺失 | 该候选不通过 |
| Word 超链接丢失 | 该候选不通过 |
| XLSX 行列缺失 | 该候选不通过 |
| 外链抓取失败 | 报告失败，宿主候选继续 |

任何故障都不得改变成“写入目标 Wiki”或“使用本地缓存作为权威”。

## 清理

所有下载、解析脚本、扫描输出和临时状态都只存在于当前运行目录。运行报告完成后删除：

```text
.picturebook-screenwriter/tmp/<run_id>/
```

若 `feishu-knowledge-store` 尚未消费 `_manifest.json`，等待 `prepare` 完成后再删除。删除前必须向用户报告确切目录。
