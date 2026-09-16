# Picture Book Screenwriter for Codex

A minimal Codex plugin for picture-book screenwriting. It packages an editorial
workflow with text-craft guidance, craft benchmark checks, and staging planning.

This repository contains only the Codex plugin and its local marketplace
manifest. It does not contain WorkBuddy runtime files, Feishu integration
configuration, credentials, or generated assets.

## Add the marketplace

In the ChatGPT desktop app plugin marketplace, add:

```text
https://github.com/QingpingYe/codex-picturebook-agent.git
```

Use `main` as the Git ref. Leave the sparse path empty if the repository is
shown as a marketplace source and it is small enough to clone fully.

From Codex CLI, you can also run:

```powershell
codex plugin marketplace add https://github.com/QingpingYe/codex-picturebook-agent.git --ref main
codex plugin add picturebook-screenwriter@picturebook-local
```

## Use the plugin

Start a new Codex chat and try:

```text
帮我从零开始策划一个 32 页、面向 3-6 岁儿童的中文绘本故事，主题是学会分享。
```

The plugin supports:

- Editorial workflow: intent routing, briefing gate, role switching, confirmation gate.
- Text craft: picture-book writing methodology and baselines.
- Craft benchmark check: quantified self-review of page text.
- Staging planner: spatial planning for page-by-page storyboards.

## Verify locally

```powershell
python C:\Users\lvan\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py .\plugins\picturebook-screenwriter
python -m unittest discover -s .\plugins\picturebook-screenwriter\skills\craft-benchmark-check\scripts -p "test_*.py" -v
node .\plugins\picturebook-screenwriter\skills\staging-planner\scripts\run_regression.js
```
