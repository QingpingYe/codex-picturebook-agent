# Picture Book Screenwriter for Codex

这是「绘本编剧工坊」的 Codex 原生插件，不依赖 WorkBuddy 团队运行时。它把 WorkBuddy 的编辑方法论迁移为普通 Codex 技能与脚本。

This repository is the active home for the Codex plugin. It packages:

- Editorial workflow
- Text craft methodology
- Craft benchmark checks
- Staging planner
- Gated illustration workflow
- Self-contained HTML preview
- 多人协作飞书知识库同步与权威检索

## Add the plugin

In the ChatGPT desktop app plugin marketplace, add:

```text
https://github.com/QingpingYe/codex-picturebook-agent.git
```

Use `main` as the Git ref. For a small repository, leave the sparse path empty.

From Codex CLI, you can also run:

```powershell
codex plugin marketplace add https://github.com/QingpingYe/codex-picturebook-agent.git --ref main
codex plugin add picturebook-screenwriter@picturebook-github
```

## Use the plugin

Start a new Codex chat and try:

```text
帮我从零开始策划一个 32 页、面向 3-6 岁儿童的中文绘本故事，主题是学会分享。
```

The plugin supports:

- Intent routing, briefing gate, role switching, confirmation gate
- Picture-book text craft methodology and baselines
- Quantified self-review of page text
- Spatial planning for page-by-page storyboards
- Structured illustration prompts, explicit image-generation confirmation, and asset tracking
- Self-contained HTML preview
- Feishu authoritative knowledge retrieval and synchronized ingestion

## Feishu knowledge setup

Each writer should:

1. Run `lark-cli auth login` with their own account.
2. Obtain read access to the source Feishu Wiki and write access to the target Feishu Wiki.
3. Copy `plugins/picturebook-screenwriter/config/feishu-knowledge-base.example.json` to `.picturebook-screenwriter/feishu-knowledge-base.json`.

The target Feishu Wiki is the authoritative shared knowledge source. Human edits are preserved. If the target Wiki is unavailable, the plugin can only use its last confirmed local cache after explicitly warning that the content is offline.

## Verify locally

```powershell
python .\scripts\run_plugin_tests.py
```

The aggregate runner executes every Python test, both Node checks, governance
validation, and package validation.

See `docs/RELEASE.md` before publishing.

## Feishu preflight

Before the first live sync, set the local config path and verify user authentication, source access, and target access:

```powershell
$env:PICTUREBOOK_KB_CONFIG = "$PWD\.picturebook-screenwriter\feishu-knowledge-base.json"
lark-cli auth login
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\sync_knowledge.py preflight --config $env:PICTUREBOOK_KB_CONFIG
```

## Feishu sync runner

The plugin uses Feishu configuration v2, which explicitly declares the source space, source root mode, target space, and target root token.

The end-to-end runtime commands are:

```powershell
$run_dir = "$PWD\.picturebook-screenwriter\runs\$(Get-Date -Format yyyyMMdd-HHmmss)"
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\sync_runner.py prepare --config $env:PICTUREBOOK_KB_CONFIG --run-dir $run_dir
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\sync_runner.py publish --config $env:PICTUREBOOK_KB_CONFIG --run-dir $run_dir
python .\plugins\picturebook-screenwriter\skills\feishu-knowledge-store\scripts\sync_runner.py verify --config $env:PICTUREBOOK_KB_CONFIG --run-dir $run_dir
```

`prepare` only writes the task-local run directory, `publish` holds the remote lock while updating the target Wiki, and `verify` is read-only.
