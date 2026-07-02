# Local Development Ledger

本目录记录当前这份 `hermes-agent` checkout 中面向本机、本团队或特定业务场景
增加的开发内容。它不是 upstream Hermes 的通用开发指南；通用规则仍以仓库根目录
`AGENTS.md` 为准。

这里的目标是做一份轻量总账：说明本地增加了什么、入口在哪里、依赖哪些外部
系统、目前是否已经合入 Hermes，以及后续维护时应先看哪些文档。

## 记录边界

适合记录：

- 本地新增 plugin、skill、profile、runbook、cron/systemd 集成。
- 面向 `odoo-hedge`、`zq_hedge` 等特定业务的 Hermes 扩展。
- 外部脚本迁入 Hermes 的计划、状态和迁移边界。
- 配置文件路径、环境变量名、运行入口和验证方式。

不适合记录：

- Slack webhook、API token、password 等 secret 明文。
- 单个业务 issue 的实现细节。
- upstream Hermes 已有通用能力的完整说明。
- 与本地定制无关的普通代码变更。

## 当前本地开发能力

| 分类 | 能力 | 主要位置/入口 | 当前状态 | 说明 |
| --- | --- | --- | --- | --- |
| Slack action bridge | `slack-socket-forwarder` | `plugins/slack-socket-forwarder/` | 已实现并部署到 `odoo-hedge-dev` gateway | 转发 Slack Block Kit interactions，服务于 `zq_hedge` Sentry autofix flow。 |
| Odoo Hedge dev assistant | `odoo-hedge-dev-assistant` | `docs/odoo-hedge-dev-assistant/` | 已有独立文档体系 | 记录 Hermes 作为 `/home/user/Repos/odoo-hedge` 开发助手的 profile、gateway、dashboard、workflow、runbook 和 inventory。 |
| Odoo Hedge workflow orchestration | `odoo-hedge-workflow` | `plugins/odoo-hedge-workflow/` | 已实现 | 动态 DAG / skill-based delivery workflow。`odoo-hedge-dev-assistant` 是项目文档层，这个 plugin 是执行层。 |
| Odoo Hedge PR review | `pr-review` / `odoo-hedge-pr-review` | `plugins/odoo-hedge-workflow/pr_review.py` | 已实现 | PR review 辅助入口，当前属于 `odoo-hedge-workflow` plugin 的子能力，不建议单独当成一个无关项目维护。 |
| Odoo Hedge sandbox server | `odoo-hedge-server` | `plugins/odoo-hedge-server/`, `skills/odoo-hedge-server/SKILL.md` | 已实现 | 通过 Slack thread、MCP server 和 skill 操作 Odoo hedge 开发/测试沙箱。 |

## 分类判断

上面的分类基本合理，但建议按“执行能力”而不是“使用场景”拆分：

- `odoo-hedge-dev-assistant` 是 umbrella 项目，负责记录 profile、systemd、
  gateway、dashboard、workflow 和运维事实。
- `odoo-hedge-workflow` 是独立 plugin，也是目前 repo 中除文档外最明确的
  Odoo Hedge 开发自动化执行层。
- `odoo-hedge-pr-review` 是 `odoo-hedge-workflow` 的专门入口，不需要从
  plugin 维度拆成另一个顶层工程。
- `odoo-hedge-server` 是另一条独立能力链：Slack command -> skill -> MCP
  server -> sandbox 操作。
- `slack-socket-forwarder` 是独立的 Slack interaction bridge，服务对象是
  `zq_hedge` Sentry autofix，不应混入 `odoo-hedge-workflow`。
## 维护规则

新增本地能力时，先在本文件加一行总账，再根据复杂度决定是否新增子目录文档：

- 简单 plugin：在 plugin 目录放 `README.md` 即可。
- 有部署事实或排障步骤：增加 `runbooks/` 文档。
- 有迁移或分阶段计划：增加 `plans/` 文档。
- 有现状盘点：增加 `inventory/` 文档。

配置和 secret 边界：

- 行为配置进入 Hermes `config.yaml` 或对应 profile config。
- token、password、webhook URL 等只放 `.env` 或外部 secret
  管理，不写入本文档。
- 文档只记录环境变量名和用途，不记录值。
