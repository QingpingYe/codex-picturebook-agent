# Picture Book Screenwriter for Codex

A minimal Codex plugin for picture-book screenwriting.

This repository is the active home for the Codex plugin. It packages:

- Editorial workflow
- Text craft methodology
- Craft benchmark checks
- Staging planner

The original WorkBuddy expert files are kept locally as a reference archive and
are not part of this repository.

## Add the plugin

In the ChatGPT desktop app plugin marketplace, add:

```text
https://github.com/QingpingYe/codex-picturebook-agent.git
```

Use `main` as the Git ref. For a small repository, leave the sparse path empty.

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

- Intent routing, briefing gate, role switching, confirmation gate
- Picture-book text craft methodology and baselines
- Quantified self-review of page text
- Spatial planning for page-by-page storyboards

## Local development

The WorkBuddy source material is available at:

```text
E:\picturebook-screenwriter\workbuddy-expert
```

## Verify locally

```powershell
python C:\Users\lvan\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py .\plugins\picturebook-screenwriter
python -m unittest discover -s .\plugins\picturebook-screenwriter\skills\craft-benchmark-check\scripts -p "test_*.py" -v
node .\plugins\picturebook-screenwriter\skills\staging-planner\scripts\run_regression.js
```
