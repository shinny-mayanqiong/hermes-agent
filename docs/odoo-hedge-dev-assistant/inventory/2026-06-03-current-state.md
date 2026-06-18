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

- 在激活 `/home/user/Repos/hermes-agent/.venv` 后：

```text
/home/user/Repos/hermes-agent/.venv/bin/hermes
Hermes Agent v0.15.1 (2026.5.29)
```

结论：

- Hermes CLI 已在项目 `.venv` 中可用。
- 使用 Hermes 前应先执行：

```bash
cd /home/user/Repos/hermes-agent
source .venv/bin/activate
```

## `odoo-hedge-dev` profile

用户已创建 profile：

```text
odoo-hedge-dev
```

已观察：

```text
Profile: odoo-hedge-dev
Path:    /home/user/.hermes/profiles/odoo-hedge-dev
Model:   gpt-5.5 (openai-codex)
Gateway: stopped
Skills:  90
.env:    exists
SOUL.md: exists
Alias:   /home/user/.local/bin/odoo-hedge-dev
```

profile 配置文件：

```text
/home/user/.hermes/profiles/odoo-hedge-dev/config.yaml
```

关键配置：

- `model.provider: openai-codex`
- `model.default: gpt-5.5`
- `agent.max_turns: 150`
- `terminal.backend: local`
- `terminal.cwd: /home/user/Repos/odoo-hedge`

结论：

- `odoo-hedge-dev` profile 已创建。
- profile alias 已创建：`/home/user/.local/bin/odoo-hedge-dev`。
- profile 已配置为在 `/home/user/Repos/odoo-hedge` 工作。
- 后续需要验证 alias 是否在普通 shell 的 PATH 中可直接运行。

## 多角色开发 profiles

已从 `odoo-hedge-dev` clone 出 V2 角色 profiles：

- `odoo-hedge-orchestrator`
- `odoo-hedge-spec`
- `odoo-hedge-spec-reviewer`
- `odoo-hedge-coder`
- `odoo-hedge-ci`
- `odoo-hedge-code-reviewer`
- `odoo-hedge-pr-reviewer`
- `odoo-hedge-closeout`
- `odoo-hedge-i18n`
- `odoo-hedge-project`

历史 V1 profiles 仍保留：

- `odoo-hedge-architect`
- `odoo-hedge-design-reviewer`
- `odoo-hedge-qa`

这些 profiles 继承了：

- `model.provider: openai-codex`
- `model.default: gpt-5.5`
- `terminal.cwd: /home/user/Repos/odoo-hedge`

profile 隔离 HOME 约定：

- 所有 `odoo-hedge-*` 开发 profiles 均使用
  `~/.hermes/profiles/<profile>/home/` 作为 worker 子进程 HOME。
- 所有当前 `odoo-hedge-*` 开发 profiles 已统一设置 `home/.gitconfig`：
  `Hermes Hedge Agent <hermes-hedge-agent@users.noreply.github.com>`。
- 所有当前 `odoo-hedge-*` 开发 profiles 已在隔离 HOME 内完成
  `gh auth login --with-token --insecure-storage`，用于 worker 内执行
  `gh pr create`、读取 PR comments 和更新 Project。
- `GH_TOKEN` 不作为 worker 认证方案；它在 Hermes terminal 环境中属于安全
  blocklist，`terminal.env_passthrough` 不会让它进入子进程。

已创建首批 role skills：

- `~/.hermes/profiles/odoo-hedge-orchestrator/skills/dev/odoo-hedge-orchestrator/SKILL.md`
- `~/.hermes/profiles/odoo-hedge-coder/skills/dev/odoo-hedge-coder/SKILL.md`

验证结果：

- `hermes -p odoo-hedge-orchestrator skills list` 可看到
  `odoo-hedge-orchestrator`，状态为 enabled。
- `hermes -p odoo-hedge-coder skills list` 可看到 `odoo-hedge-coder`，
  状态为 enabled。

## OpenAI Codex OAuth

曾观察到 `odoo-hedge-dev` profile 调用 `openai-codex` 时返回：

```text
HTTP 401
token_expired
Provided authentication token is expired. Please try signing in again.
```

处理方式：

```bash
cd /home/user/Repos/hermes-agent
source .venv/bin/activate

hermes -p odoo-hedge-dev auth logout openai-codex
hermes -p odoo-hedge-dev auth add openai-codex
```

用户确认：

- 重新登录后已成功。

结论：

- `openai-codex` token 过期时，`auth status` 显示 logged in 不一定代表调用可用。
- 遇到 `token_expired`，优先在当前 profile 下重新 `auth add openai-codex`。

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

1. 为 Architect、Design Reviewer、Spec Reviewer、QA 补充 role skills。
2. 定义第一版固定 Kanban 流程模板。
3. 运行一个低风险 dry run。
4. 接入 Slack 通知。
