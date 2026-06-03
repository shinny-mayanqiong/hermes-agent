# 2026-06-03 `.codex` 开发流程资产盘点

## 范围

本盘点记录 `/home/user/Repos/odoo-hedge/.codex` 中与开发人员 workflow 相关的
资产，并给出迁移到 Hermes `odoo-hedge-dev` profile 的初步分级。

用户确认：

- 当前开发主力是 Codex。
- `~/.hermes/skills/domain/odoo-hedge*` 是业务用户使用的 skills，不作为开发人员
  workflow 入口。
- `odoo-hedge/.cursor/skills` 暂时忽略。

## `.codex` 基础文件

已观察到：

- `.codex/README.md`
- `.codex/config.toml`
- `.codex/rules/default.rules`

`.codex/README.md` 说明：

- repo-specific skills 放在 `.codex/skills/<skill-name>/`。
- 个人 auth、approval、session、logs、cache、machine-specific config 不进入 git。

`.codex/config.toml` 关键点：

- `sandbox_mode = "workspace-write"`
- trusted project：`/home/user/Repos/odoo-hedge`
- writable roots：
  - `/home/user/Repos/odoo-hedge`
  - `/home/user/Repos/odoo-hedge-worktrees`
- network access 开启。
- shell environment 只保留开发常用基础变量。
- features 开启 `shell_snapshot`、`shell_tool`、`multi_agent`、`personality`。

`.codex/rules/default.rules` 关键点：

- 已允许 `source /home/user/Repos/odoo-hedge/.venv/bin/activate`
- 已允许 `gh`
- 已允许 `git`
- 已允许 `sed`
- 已允许 `rg`

## Skills 总览

已观察到的 repo-specific Codex skills：

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
- `presentation-skill`
- `project8-hygiene-audit`
- `spec-blueprint-prompts`
- `spec-freeze`
- `translate-hedge-zh-cn`
- `weekly-execution-sync`
- `weekly-sync-publish`

## 初步迁移分级

### A 类：可作为通用开发流程直接复用或轻量接入

这些 skills 主要是流程约束，适合优先接入 Hermes：

- `brainstorm-spec`
- `spec-freeze`
- `agents-implement-verify`
- `spec-blueprint-prompts`

处理建议：

- 先检查当前 Hermes 是否已有同名或等价 skills。
- 如果已有，避免重复接入。
- 如果 `odoo-hedge` 版本包含项目专属约束，则迁移为 `odoo-hedge` dev workflow
  的依赖或派生文档。

### B 类：核心开发交付流程，应该迁移成 Hermes dev skills

这些是开发人员高频 workflow，应优先迁移：

- `issue-worktree-bootstrap`
- `hedge-issue-delivery-loop`
- `hedge-ci-watch-repair-loop`
- `gh-pr-review-followup`
- `issue-closeout-sync`
- `project8-hygiene-audit`
- `gh-project-task-status`
- `translate-hedge-zh-cn`

处理建议：

- 不要原样复制后立即启用。
- 先统一成 Hermes 命名和入口，例如：
  - `odoo-hedge-dev`
  - `odoo-hedge-issue-workflow`
  - `odoo-hedge-worktree`
  - `odoo-hedge-ci`
  - `odoo-hedge-pr-review`
  - `odoo-hedge-project8`
  - `odoo-hedge-i18n`
- 每个 skill 应明确：
  - 必须先读 `/home/user/Repos/odoo-hedge/AGENTS.md`
  - 必须先读 `/home/user/Repos/odoo-hedge/hedge_docs/README.md`
  - 实际任务状态事实源仍是 GitHub Issue / Project 8
  - 普通开发任务不写到 Hermes 文档目录

### C 类：自动化/调度流程，先转成 runbook，再考虑 Hermes cron 或 gateway

这些流程较重，适合作为第二阶段迁移：

- `daily-ui-audit-publish`
- `exploratory-ui-audit-publish`
- `weekly-execution-sync`
- `weekly-sync-publish`
- `playwright-ui-debug-loop`

处理建议：

- 先写 runbook，记录手动执行路径。
- 再决定是否接 Hermes `cron`、Slack 通知、dashboard 操作入口。
- 涉及 UI/browser/DB reset 的流程要保留强验证和停止条件。

### D 类：高风险操作，优先做 plugin/tool 或 runbook，不能只靠自然语言 skill

这些操作会改数据库、创建 worktree、改 Project 状态或 push，需要强边界：

- `hedge-db-reset`
- `issue-worktree-bootstrap`
- `weekly-sync-publish`
- `daily-ui-audit-publish`
- `exploratory-ui-audit-publish`

处理建议：

- 先用 runbook 明确参数、前置条件、验证和回滚。
- 高频后再做 Hermes local plugin 工具，例如：
  - `hedge_worktree_create`
  - `hedge_ci_check`
  - `hedge_db_reset`
  - `hedge_project8_status`
- destructive 或数据库相关动作必须保留确认步骤。

### E 类：暂缓迁移

- `presentation-skill`

处理建议：

- 该 skill 与 `odoo-hedge` 日常代码开发关系弱。
- 暂不作为 `odoo-hedge-dev` bootstrap 范围。

## 迁移原则

- Codex 仍是开发主力，不把迁移理解为“替换 Codex”。
- Hermes 的价值是统一 profile、CLI/TUI、Slack、dashboard、session、logs、
  skills、cron 和 gateway。
- 迁移应先迁移 workflow 入口和 guardrails，再考虑工具化。
- 不把业务用户 skills 和开发人员 workflow 混用。
- 不把 `.cursor/skills` 纳入第一阶段。
- 不改 Hermes core，除非 profile、external skills、local plugin、hooks 都无法满足。

## 初始建议

第一批迁移对象：

1. `odoo-hedge-dev`：开发总入口和 guardrails。
2. `odoo-hedge-worktree`：基于 `issue-worktree-bootstrap`。
3. `odoo-hedge-ci`：基于 `hedge-ci-watch-repair-loop` 和 `scripts/ci_check.py`。
4. `odoo-hedge-issue-workflow`：基于 `hedge-issue-delivery-loop`。
5. `odoo-hedge-i18n`：基于 `translate-hedge-zh-cn`。

第一批不做：

- Slack gateway 自动化。
- dashboard/API Server 集成。
- DB reset 工具化。
- weekly/daily scheduled publish。
