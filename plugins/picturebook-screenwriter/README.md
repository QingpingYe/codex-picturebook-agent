# Picture Book Screenwriter for Codex

这是「绘本编剧工坊」的 Codex 原生插件，不依赖 WorkBuddy 团队运行时。当前支持编辑式入口、飞书权威知识库、文字工艺、量化自检、分镜规划、结构化插画提示词、显式确认后的图片生成和插画导出预览。

## 已支持

- 主编式入口工作流：意图识别、简报门、角色切换、确认门、版本化落盘
- 可选阶段 DAG：Codex 多 Agent 工具可用时派发子 Agent；不可用时顺序降级
- `text-craft`：儿童绘本文本创作方法论
- `craft-benchmark-check`：十五项文字工艺指标量化自测
- `staging-planner`：跨页分镜站位与空间一致性规划
- `image-prompt-architect`：结构化插画提示词；只输出 JSON，不生成图片
- `image-generate`：仅在用户明确确认后调用图片 API
- `illustration-export`：资产登记、本地 HTML 预览与显式图片内嵌
- `session-export`：仅在用户明确要求时导出会话证据，且必须使用用户提供的绝对输出目录
- `text_quality_prefilter`：对全部活动红线与逐页文本质量做 Jev 预筛，只把高风险、灰区和冲突项升级普通 LLM
- `knowledge_relevance`：把权威知识切块并甄别相关性，硬约束块永不被过滤
- `knowledge-loader`：只读检索多人协作飞书权威知识库
- `feishu-knowledge-store`：人工优先合并、冲突队列与远端租约
- `jev-decision-runtime`：共享 Jev 决策运行器；只在用户选择 Jev 辅助路径后调用，负责凭证门、请求契约、重试、恢复点、租约、原子终态与成本估算
- `lexile-check`：仅使用用户提供的实测结果

## 暂不支持

- WorkBuddy TeamCreate / SendMessage / 持久多 Agent 团队运行时
- 原生 hooks 与平台级硬约束
- 未确认的默认图片 API 调用
- Jev 不参与生成、润色、修改建议或插画提示词；知识相关性筛选与文本质量预筛的 operation 策略在后续阶段落地

运行时强制、脚本校验与提示词约束的边界见 `docs/ENFORCEMENT.md`。

## 飞书知识库

本插件不得编辑原始资料库，只能从它读取候选内容。多人同步时，目标飞书 Wiki 是唯一共享权威；人工修改优先于 AI 内容。若目标 Wiki 不可用，只能使用最后确认的本地缓存，且必须在提示中说明“离线”和“非权威”。

### Configuration discovery

Configuration is local to each user or workspace. The plugin ships only `config/feishu-knowledge-base.example.json`; that template is never discovered.

Resolution order:

1. Explicit `--config`.
2. `PICTUREBOOK_KB_CONFIG`.
3. The nearest ancestor of `<workspace>` containing `feishu-knowledge-base.json`.
4. Windows: `%APPDATA%\picturebook-screenwriter\feishu-knowledge-base.json`; macOS: `~/Library/Application Support/picturebook-screenwriter/feishu-knowledge-base.json`; Linux: `${XDG_CONFIG_HOME:-~/.config}/picturebook-screenwriter/feishu-knowledge-base.json`.
5. deprecated: `~/.picturebook-screenwriter/feishu-knowledge-base.json`.

A project subdirectory inherits a config from its nearest ancestor. Do not commit a real configuration to a repository, and do not store one in a plugin cache. `config-status` reports `origin`, `path`, and `searched` so the selected file is explicit.

Target Wiki roots support two modes. Use `target.root_mode: "space"` when the four system containers live at the Wiki space root; omit `target.root_token` in this mode. Use `target.root_mode: "node"` when they live under a specific Wiki node and set `target.root_token` to that node token. The example uses space mode with generic placeholders.

## Feishu runtime commands

配置文件使用 schema v2，明确区分 `source` 与 `target`。

```powershell
python .\skills\feishu-knowledge-store\scripts\lark_cli_bootstrap.py
python .\skills\feishu-knowledge-store\scripts\store_cli.py config-status --workspace <workspace>
python .\skills\knowledge-loader\scripts\authority_cli.py load --workspace <workspace> --project-id <project_id> --series-id <series_id> --page-types worldview,characters,content_spec
python .\skills\feishu-knowledge-store\scripts\sync_runner.py prepare --config <config> --run-dir <run_dir>
python .\skills\feishu-knowledge-store\scripts\sync_runner.py publish --config <config> --run-dir <run_dir>
python .\skills\feishu-knowledge-store\scripts\sync_runner.py verify --config <config> --run-dir <run_dir>
```

`authority_cli.py` 只做只读检索。页面 revision 落后于远端索引时读取失败；页面前移但索引尚未同步时输出 `index_synced=false` 和警告，且不得覆盖最后确认缓存。离线缓存不是权威版本，只有在用户显式批准后加 `--allow-offline-cache` 使用，输出必须继续说明“非权威”。

`lark_cli_bootstrap.py` 支持 `1.0.95` / `1.0.96`，按 `LARK_CLI_PATH`、`PATH`、`%APPDATA%\npm`、`%ProgramFiles%\nodejs` 和 Unix 标准 bin 目录的顺序查找；没有作者专用的盘符 fallback。缺 CLI 时会输出官方安装命令；只有在用户明确批准后，才加 `--install` 执行 `npx @larksuite/cli@latest install`。安装机需要 Node.js 16+，且每个用户安装后仍需本人执行 `lark-cli auth login`。

## 安装

从 GitHub marketplace 安装：

```powershell
codex plugin marketplace add QingpingYe/codex-picturebook-agent --ref main
codex plugin add picturebook-screenwriter@picturebook-github
```

如果 GitHub marketplace 已注册，只需执行第二条。

## 使用

安装后选择或调用 Picture Book Screenwriter，输入：

```text
帮我从零开始策划一个 32 页、面向 3-6 岁儿童的中文绘本故事，主题是学会分享。
```

也可以使用默认提示：

```text
帮我从零开始策划一个绘本故事
```

## 验证

```powershell
python .\scripts\run_plugin_tests.py
```

Windows 上如果 `python` 指向 Microsoft Store 存根，请先使用可用的 Python 3.10+ 解释器。

## Jev 辅助路径

入口工作流的第一件事是询问是否启用 Jev 辅助。选择启用后，相关故事文本和知识片段会发送给 TypeSafe Jev 做知识筛选与文本质检预筛；创作、解释性终审和修改建议仍由普通 LLM 完成。

凭证只从环境变量 `TYPESAFE_API_KEY` 读取。若本机尚未配置，工作流会暂停并提示在本机环境中安全设置该变量，然后回复“已配置”从原任务继续：

```powershell
$env:TYPESAFE_API_KEY = "<your-key>"
```

**不得把密钥粘贴到对话中**，也不要把它写进配置、仓库或插件目录。endpoint 固定为 `https://api.typesafe.ai/v1/systemone`，不接受 base URL 覆盖，禁用 redirect。模型固定为 `jev-1.13.0`，不使用会漂移的 `latest` 别名。

未校准期间所有 operation 均为 `experimental`：`screened_clear` 只是 Jev 的候选结论，不缩减普通 LLM 的复核范围。

两条 operation 都有可直接运行的入口：知识相关性甄别用 `python skills/knowledge-loader/scripts/relevance_cli.py …`，文本质量预筛用 `python skills/pre_output-baseline/scripts/screening_cli.py …`。两者都只在用户选择 Jev 辅助路径后由技能指令调用；预筛的 `screened_clear` 与升级包都在报告的 JSON 里，退出码为 0 不代表通过。
