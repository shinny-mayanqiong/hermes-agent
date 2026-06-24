# Runbooks

本目录记录 Hermes 作为 `odoo-hedge` 开发助手时的日常操作手册。

Runbook 应该让一次重复操作可以按步骤完成，例如启动、停止、检查、排障、恢复。
它不负责解释所有背景，也不替代 decision 或 plan。

## 适合放在这里的内容

- 启动 Hermes CLI/TUI 的步骤
- 启动 `hermes dashboard --tui` 的步骤
- 启动、停止、重启、检查 gateway 的步骤
- Slack App / Socket Mode 配置步骤
- DeepSeek API key 检查步骤
- `codex_app_server` 可用性检查步骤
- 常见错误排障

## 不适合放在这里的内容

- 长期设计理由
- 大段架构分析
- `odoo-hedge` 业务功能实现计划
- 未经验证的一次性命令
- 密钥明文

## 文档格式建议

每个 runbook 建议包含：

- 用途
- 适用场景
- 前置条件
- 步骤
- 验证
- 常见问题
- 回滚或停止方式

文件名建议使用：

```text
short-operation-name.md
```

例如：

```text
dashboard-tui.md
gateway-slack.md
codex-runtime-check.md
```

## 仍缺的 runbook

- Hermes CLI/TUI 启动检查
- `odoo-hedge` profile 切换与验证
- provider / model 切换与验证
- Slack App / Socket Mode 配置检查
- dashboard 认证与网络边界检查

## 当前 runbooks

- `local-systemd-deployment.md` - 本机 `odoo-hedge-dev` gateway/dashboard
  的 `systemd --user` 部署、状态检查、日志和重启方式。
- `odoo-hedge-dev-profile.md` - profile 配置、隔离 HOME、GitHub CLI 和
  commit 身份约定。
- `odoo-hedge-workflow-plugin.md` - `odoo-hedge-workflow` plugin 的
  `start` / `tick` / `status` 使用方式。
