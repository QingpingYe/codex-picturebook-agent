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
- `knowledge-loader`：只读检索多人协作飞书权威知识库
- `feishu-knowledge-store`：人工优先合并、冲突队列与远端租约
- `lexile-check`：仅使用用户提供的实测结果

## 暂不支持

- WorkBuddy TeamCreate / SendMessage / 持久多 Agent 团队运行时
- 原生 hooks 与平台级硬约束
- 未确认的默认图片 API 调用

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
python <codex-home>\skills\.system\plugin-creator\scripts\validate_plugin.py .\plugins\picturebook-screenwriter
python -m unittest discover -s .\plugins\picturebook-screenwriter\skills\craft-benchmark-check\scripts -p "test_*.py" -v
node .\plugins\picturebook-screenwriter\skills\staging-planner\scripts\run_regression.js
python .\scripts\governance_check.py
python .\scripts\package_check.py
```

Windows 上如果 `python` 指向 Microsoft Store 存根，请先使用可用的 Python 3.10+ 解释器。
