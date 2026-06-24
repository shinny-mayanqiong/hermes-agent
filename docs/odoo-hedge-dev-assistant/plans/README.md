# Plans

本目录记录把 Hermes 配置成 `odoo-hedge` 开发助手的阶段性实施计划。

这里的计划服务于“开发助手系统本身”，不是普通 `odoo-hedge` 业务功能开发任务。
实际业务功能、bug fix、spec、验收记录，应继续放在 `odoo-hedge` 仓库自己的
GitHub Issue / Project 8 / `hedge_docs/` 体系中。

## 适合放在这里的内容

- Hermes profile 配置计划
- `odoo-hedge` 专用 Hermes skills 迁移计划
- Slack gateway 接入计划
- web dashboard / API Server 接入计划
- DeepSeek 辅助模型验证计划
- 本地 plugin / hook 改造计划

## 不适合放在这里的内容

- `odoo-hedge` 普通业务功能需求
- 单个 GitHub Issue 的业务规格
- Odoo 模块实现细节
- 测试验收事实源
- 密钥、token、password、private endpoint

## 文档格式建议

每个计划文档建议包含：

- 目标
- 背景
- 范围
- 不做什么
- 前置条件
- 分阶段步骤
- 验证方式
- 风险与回滚
- 后续决策点

文件名建议使用：

```text
YYYY-MM-DD-short-topic.md
```

例如：

```text
2026-06-03-bootstrap-hedge-dev-profile.md
```

## 初始计划候选（历史）

- 建立 `odoo-hedge` 专用 Hermes profile
- 迁移 `.cursor/skills/hedge_ci_check` 到 Hermes skill
- 为 `odoo-hedge` 创建开发工作流 skill
- 配置 Slack gateway 的用户和频道 allowlist
- 建立 dashboard `--tui` 日常使用 runbook

其中 profile、Slack gateway、dashboard 和 V2 workflow plugin 已进入当前运行
状态。实际部署和运维步骤见 `../runbooks/local-systemd-deployment.md`。

## 当前重点文档

- `2026-06-18-task-t_2a9c7cd8-retrospective.md` - 复盘普通 task 绕过
  多角色流程的问题。
- `2026-06-18-dynamic-dag-workflow-v1.md` - 定义动态 DAG workflow、
  orchestrator tick、阶段状态机和 worker role policy。
- `2026-06-18-skill-based-delivery-workflow-v2.md` - 确定后续目标流程：
  spec discussion / spec freeze / blueprint / 文档 review / implementation /
  CI / 本地 code review / PR comments / closeout，并映射到 profiles 和
  repo-local Codex skills。
