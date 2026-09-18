# Picture Book Screenwriter for Codex

这是「绘本编剧工坊」的 Codex 原生插件，不依赖 WorkBuddy 团队运行时。当前支持编辑式入口、飞书权威知识库、文字工艺、量化自检和分镜规划；后续阶段会逐步补齐完整创作、质检、插画和导出能力。

## 已支持

- 主编式入口工作流：意图识别、简报门、角色切换、确认门、版本化落盘
- `text-craft`：儿童绘本文本创作方法论
- `craft-benchmark-check`：十五项文字工艺指标量化自测
- `staging-planner`：跨页分镜站位与空间一致性规划
- `knowledge-loader`：只读检索多人协作飞书权威知识库
- `feishu-knowledge-store`：人工优先合并、冲突队列与远端租约

## 暂不支持

- 插画与素材图生成
- HTML 预览导出
- 多 Agent / 子 Agent 团队执行
- hooks 与约束门禁
- Lexile 检测
- 会话取证导出

## 飞书知识库

本插件不得编辑原始资料库，只能从它读取候选内容。多人同步时，目标飞书 Wiki 是唯一共享权威；人工修改优先于 AI 内容。若目标 Wiki 不可用，只能使用最后确认的本地缓存，且必须在提示中说明“离线”和“非权威”。

## Feishu runtime commands

配置文件使用 schema v2，明确区分 `source` 与 `target`。

```powershell
python .\skills\feishu-knowledge-store\scripts\lark_cli_bootstrap.py
python .\skills\feishu-knowledge-store\scripts\sync_runner.py prepare --config <config> --run-dir <run_dir>
python .\skills\feishu-knowledge-store\scripts\sync_runner.py publish --config <config> --run-dir <run_dir>
python .\skills\feishu-knowledge-store\scripts\sync_runner.py verify --config <config> --run-dir <run_dir>
```

`lark_cli_bootstrap.py` 支持 `1.0.95` / `1.0.96`，按 `LARK_CLI_PATH`、PATH、常见 C 盘 npm 全局位置（`%APPDATA%\npm` 与 `%ProgramFiles%\nodejs`）、`C:\lark-cli`、`D:\lark-cli` 的顺序查找。缺 CLI 时会输出官方安装命令；只有在用户明确批准后，才加 `--install` 执行 `npx @larksuite/cli@latest install`。安装机需要 Node.js 16+，且每个用户安装后仍需本人执行 `lark-cli auth login`。

## 安装

在仓库根目录执行：

```powershell
codex plugin marketplace add E:\picturebook-screenwriter
codex plugin add picturebook-screenwriter@picturebook-local
```

如果本地 marketplace 已注册，只需执行第二条。

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
python C:\Users\lvan\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py .\plugins\picturebook-screenwriter
python -m unittest discover -s .\plugins\picturebook-screenwriter\skills\craft-benchmark-check\scripts -p "test_*.py" -v
node .\plugins\picturebook-screenwriter\skills\staging-planner\scripts\run_regression.js
```

Windows 上如果 `python` 指向 Microsoft Store 存根，请先使用可用的 Python 3.10+ 解释器。
