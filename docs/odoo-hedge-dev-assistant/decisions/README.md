# Decisions

本目录记录关于 Hermes 作为 `odoo-hedge` 开发助手的长期决策。

这里的 decision 用来回答“为什么这样配置或设计”，避免以后反复重新讨论同一个
选择。它不是任务列表，也不是操作手册。

## 适合放在这里的内容

- 主模型和辅助模型选择
- 是否启用 `codex_app_server`
- 是否优先使用 external skills / local plugin，而不是改 Hermes core
- Slack gateway 的触发策略、allowlist 策略
- web dashboard 与 API Server 的边界
- 本地文档、skills、plugins 的存放策略

## 不适合放在这里的内容

- 临时 todo
- 一次性操作命令
- 单个 GitHub Issue 的业务决策
- 未验证的猜测
- 秘密信息

## 文档格式建议

每个 decision 建议包含：

- 标题
- 状态：`proposed`、`accepted`、`superseded`
- 日期
- 背景
- 决策
- 影响
- 替代方案
- 后续复审条件

文件名建议使用：

```text
NNNN-short-topic.md
```

例如：

```text
0001-separate-hedge-assistant-docs-from-hermes-upstream.md
```

## 已形成的初始决策

- 本目录与 Hermes upstream `AGENTS.md` 分离。
- 本目录正文默认使用中文，保留必要英文术语。
- 普通 `odoo-hedge` 开发任务不迁移到本目录。
- 优先使用 profile、external skills、local plugin、hooks；除非必要，不改
  Hermes core。
