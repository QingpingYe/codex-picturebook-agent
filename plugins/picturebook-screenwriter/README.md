# Picture Book Screenwriter for Codex

这是「绘本编剧工坊」的 Codex 最小版插件。它保留绘本创作核心闭环，不包含团队运行时和外部系统集成。

## 已支持

- 主编式入口工作流：意图识别、简报门、角色切换、确认门、版本化落盘
- `text-craft`：儿童绘本文本创作方法论
- `craft-benchmark-check`：十五项文字工艺指标量化自测
- `staging-planner`：跨页分镜站位与空间一致性规划

## 暂不支持

- 外部知识库同步
- 插画与素材图生成
- HTML 预览导出
- 多 Agent / 子 Agent 团队执行
- hooks 与约束门禁
- Lexile 检测
- 会话取证导出

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
