# 2026-06-03 当前状态盘点

## 范围

本盘点记录当前开发机器上 Hermes 作为 `odoo-hedge` 开发助手的初始状态。

盘点对象：

- Hermes 当前分支与文档目录
- `~/.hermes/config.yaml` 中与开发助手有关的配置
- Codex CLI 可用性
- Hermes CLI 可用性
- 现有 `odoo-hedge` 相关 skills
- `odoo-hedge/.codex` 可复用资产

不盘点密钥值、token、password、private endpoint。

## 当前仓库状态

- 当前仓库：`/home/user/Repos/hermes-agent`
- 当前分支：`my-self-main`
- 远端跟踪：`origin/my-self-main`
- 本目录用途：记录“让 Hermes 成为 `odoo-hedge` 开发助手”的本机集成项目。
- upstream Hermes 开发说明仍以根目录 `AGENTS.md` 为准。

## Hermes 配置摘要

配置文件：`~/.hermes/config.yaml`

已观察到的关键配置：

- `model.provider: openai-codex`
- `model.default: gpt-5.5`
- `model.base_url: https://chatgpt.com/backend-api/codex`
- `toolsets: ["hermes-cli"]`
- `terminal.backend: local`
- `terminal.cwd: .`
- `skills.external_dirs: []`
- `delegation.provider: ""`
- `delegation.model: ""`
- `approvals.mode: manual`

结论：

- 当前主模型配置已经偏向 Codex。
- 当前 `terminal.cwd` 还没有指向 `/home/user/Repos/odoo-hedge`。
- 当前还没有把 repo-specific skills 或外部 skills 目录接入 Hermes。
- 当前 delegation 尚未单独配置 DeepSeek 或其他辅助 provider。

## Codex CLI

已观察：

```text
/home/user/.nvm/versions/node/v24.13.0/bin/codex
codex-cli 0.136.0
```

运行 `codex --version` 时出现提示：

```text
WARNING: proceeding, even though we could not update PATH: Read-only file system (os error 30)
```

结论：

- Codex CLI 可用。
- PATH 更新警告需要在后续 runbook 或 bootstrap 阶段确认是否影响 Hermes 的
  `codex_app_server` runtime。

## Hermes CLI

已观察：

- 当前 shell 中 `command -v hermes` 没有输出。

结论：

- `hermes` 命令当前没有可靠出现在 PATH。
- 下一步必须先确认 Hermes 是源码运行、editable install、pip install，还是需要补
  shell PATH。

## 现有业务用户 skills

路径：`~/.hermes/skills/domain/odoo-hedge*`

已观察到的 `SKILL.md`：

- `odoo-hedge`
- `odoo-hedge-api`
- `odoo-hedge-assistant`
- `odoo-hedge-contract-writes`
- `odoo-hedge-contracts`
- `odoo-hedge-correlation`
- `odoo-hedge-futures-knowledge`
- `odoo-hedge-hedging-accounting`
- `odoo-hedge-imports-file`
- `odoo-hedge-imports-ocr`
- `odoo-hedge-kuaiqi-usage`
- `odoo-hedge-master-data`
- `odoo-hedge-report-design`
- `odoo-hedge-reports-apps`
- `odoo-hedge-tenant-admin`
- `odoo-hedge-trading`

用户确认：

- 这些 skills 不是开发人员使用的。
- 它们面向使用 `odoo-hedge` 网站的业务人员。

结论：

- 不把这些 skills 当作开发工作流主资产。
- 后续可以保留为业务语义参考，但不应让它们主导开发任务路由。
- 开发人员使用的 skills 应另建，例如 `odoo-hedge-dev`、`odoo-hedge-ci`、
  `odoo-hedge-issue-workflow`。

## `.cursor/skills`

用户确认：

- `odoo-hedge/.cursor/skills` 可以忽略不计。
- 当前开发主力是 Codex。

结论：

- 暂不迁移 `.cursor/skills`。
- 后续如发现其中某个流程仍有价值，再作为单独计划处理。

## `.codex` 资产

路径：`/home/user/Repos/odoo-hedge/.codex`

已观察到的关键文件：

- `.codex/README.md`
- `.codex/config.toml`
- `.codex/rules/default.rules`

已观察到的 repo-specific Codex skills 示例：

- `agents-implement-verify`
- `brainstorm-spec`
- `daily-ui-audit-publish`
- `exploratory-ui-audit-publish`
- `gh-pr-review-followup`
- `gh-project-task-status`
- `hedge-ci-watch-repair-loop`
- `hedge-db-reset`
- `hedge-issue-delivery-loop`
- `issue-closeout-sync`
- `issue-worktree-bootstrap`
- `playwright-ui-debug-loop`
- `project8-hygiene-audit`
- `spec-blueprint-prompts`
- `spec-freeze`
- `translate-hedge-zh-cn`
- `weekly-execution-sync`
- `weekly-sync-publish`

结论：

- `.codex` 是当前开发工作流的主要既有资产来源。
- 下一步应优先研究 `.codex/config.toml`、`.codex/rules/default.rules` 和上述
  Codex skills，判断哪些应通过 Hermes `skills.external_dirs` 直接接入，哪些应改写
  成 Hermes-specific dev skills 或 local plugin。

## 当前策略结论

- 开发主力：Codex。
- 业务用户 skills：保留但不作为开发工作流入口。
- `.cursor/skills`：暂时忽略。
- `.codex`：作为开发工作流资产的主要输入。
- Hermes core：暂不改，优先使用 profile、external skills、local plugin、hooks。

## 下一步建议

1. 修复或确认 `hermes` CLI 可用性。
2. 创建 `odoo-hedge` 专用 Hermes profile。
3. 将 `terminal.cwd` 指向 `/home/user/Repos/odoo-hedge`。
4. 研究 `.codex` skills 接入 Hermes 的方式。
5. 创建开发人员专用 Hermes skills，而不是复用业务用户 domain skills。
