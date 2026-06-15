# 2026-06-04 Delivery Workflow V1

## 目标

把 Hermes 作为 `odoo-hedge` 的交付编排层，但 V1 不从“创建 issue”开始。

V1 的主路径从已有 GitHub issue 开始，第一步是创建隔离 worktree，然后复用
`odoo-hedge` 本地已有 `.codex/skills` 完成交付链路。

## 非目标

V1 不做这些事：

- 不默认把自然语言需求自动变成 GitHub issue。
- 不重写 `odoo-hedge/.codex/skills` 已有能力。
- 不一开始就追求全自动无人值守。
- 不自动 merge PR。
- 不把 issue creation 放进默认 delivery graph。

如果用户只有自然语言需求、没有 issue，Orchestrator 应该先阻塞并要求用户确认：

- 使用已有 issue number；或
- 明确要求进入 issue draft / create issue 流程。

## 入口模式

V1 支持三个入口，优先级从高到低：

### A. 已有 issue

这是默认主入口。

输入：

- GitHub issue number
- 可选 phase / step / topic

流程从 `Bootstrap Worktree` 开始。

### B. 已有 branch / worktree

用于继续中断的开发。

输入：

- branch name
- worktree path
- issue number 或 PR number

流程从 `Rebuild Context / Architect Plan` 或 `Implementation` 继续。

### C. 已有 PR

用于 CI、review comments、closeout。

输入：

- PR number
- branch name

流程从 `CI Watch` 或 `PR Comments Follow-up` 继续。

### D. 没有 issue 的自然语言需求

不是 V1 默认主路径。

Orchestrator 应该创建 blocked task，要求用户确认是否使用
`gh-project-task-status` 创建 issue。确认前不继续进入 worktree / coding。

## 主流程

已有 issue 的默认流程：

```text
Root Delivery Task
  -> Bootstrap Worktree
  -> Rebuild Issue Context / Architect Plan
  -> Design Review
  -> Implementation
  -> Spec Review
  -> QA
  -> Create or Sync PR
  -> CI Watch
  -> PR Comments Follow-up
  -> Closeout Report
```

这个流程的第一步是 `Bootstrap Worktree`，不是 issue creation。

## 复用已有 Skills

Orchestrator 负责串联，不重写细节。

优先复用这些 `odoo-hedge/.codex/skills`：

- `issue-worktree-bootstrap`：根据 issue 创建 branch / worktree / DB / port 资源。
- `hedge-issue-delivery-loop`：执行 issue 的实现、验证、commit、push、PR sync。
- `hedge-ci-watch-repair-loop`：push 后等待 CI，失败则进入 repair loop。
- `gh-pr-review-followup`：处理 PR review comments。
- `issue-closeout-sync`：同步 closeout evidence、issue、Project 8、progress docs。
- `gh-project-task-status`：仅在用户明确要创建 issue 时使用。

Hermes role skills 只补充编排规则和边界，不复制这些 skills 的完整逻辑。

## Profiles

当前使用这些 profiles：

- `odoo-hedge-dev`：基础 profile、人工入口、dashboard / gateway runtime。
- `odoo-hedge-orchestrator`：流程编排，不写代码。
- `odoo-hedge-architect`：读取 issue / repo docs，输出 plan。
- `odoo-hedge-design-reviewer`：审核 plan。
- `odoo-hedge-coder`：worktree bootstrap、实现、commit、push、PR sync。
- `odoo-hedge-spec-reviewer`：对照 issue / plan 验收实现。
- `odoo-hedge-qa`：测试、CI watch、质量检查。

V1 不急着新增 `pr-agent`、`ci-watcher`、`closeout` profiles；先复用现有 profiles。

## Kanban Task Contract

每个 root delivery task 必须写清：

- 入口类型：`existing_issue` / `existing_worktree` / `existing_pr` / `needs_issue`
- issue number、branch、worktree path、PR number 中已知的值
- 当前 phase
- human gate
- next task

每个 child task 必须包含：

- parent/root task id
- assignee profile
- required inputs
- expected outputs
- forbidden actions
- unblock condition
- next visible milestone

每个 task 完成时必须输出：

- `Latest summary`
- artifact links / paths
- verification result
- blocker 或 next action

## Worktree Contract

真实开发默认先创建 worktree。

规则：

- base ref：`master`
- branch 格式：`codex/issue-<num>-<short-topic>`
- 创建命令：

```bash
cd /home/user/Repos/odoo-hedge
./scripts/codex-worktree.sh create <branch> master --no-codex
```

worktree task 必须记录：

- branch name
- absolute worktree path
- `ODOO_DB_NAME`
- derived test DB：`"$ODOO_DB_NAME"_test`
- `PYTHON_VENV_PATH`
- `ODOO_CONFIG`
- http / gevent ports

禁止：

- 不在 `master` 上写代码。
- 不硬编码 DB name。
- 不自动 purge DB。
- 不覆盖已有 worktree。

## Human Gates

V1 保留这些人工确认点。

### Gate A：没有 issue

如果用户没有提供 issue number，阻塞。

用户确认后才可以调用 `gh-project-task-status` 创建 issue。

### Gate B：Plan Approval

Architect plan 后阻塞。

用户确认：

- scope 是否正确
- 是否进入开发
- 是否要拆 phase

### Gate C：High Risk Operation

以下情况必须阻塞：

- DB reset
- migration
- destructive command
- 修改 `odoo/` 或 `addons/`
- 大范围 refactor
- 修改生产数据或外部服务状态

### Gate D：PR Comment Automation

PR review comments 先分类：

- `adopt`
- `decline`
- `needs clarification`

自动修复前必须等待用户确认 adopt set。

### Gate E：Final Closeout

Closeout 只汇报结果和建议下一步，不自动 merge。

## 阶段职责

### Bootstrap Worktree

Assignee：`odoo-hedge-coder`

复用：`issue-worktree-bootstrap`

输出：

- branch
- worktree path
- DB / port / config summary
- kickoff commands

### Rebuild Issue Context / Architect Plan

Assignee：`odoo-hedge-architect`

输入：

- issue number
- worktree path
- issue body / linked docs

输出：

- implementation-ready plan
- acceptance criteria
- verification commands
- risk / rollback
- phase split decision

### Design Review

Assignee：`odoo-hedge-design-reviewer`

输出：

- `APPROVED` 或 `REQUEST_CHANGES`
- 必须修改的 plan 问题

### Implementation

Assignee：`odoo-hedge-coder`

复用：`hedge-issue-delivery-loop`

输出：

- changed files
- commits
- local verification
- PR sync state

### Spec Review

Assignee：`odoo-hedge-spec-reviewer`

输出：

- `spec_compliance=true/false`
- gaps
- 是否进入 QA

### QA

Assignee：`odoo-hedge-qa`

输出：

- `qa_passed=true/false`
- `python scripts/ci_check.py` 结果
- 最小相关 Odoo tests 结果
- translation / XML / manifest / security checks

### Create or Sync PR

Assignee：`odoo-hedge-coder`

复用：`hedge-issue-delivery-loop`

输出：

- PR URL
- PR body summary
- pushed commits

### CI Watch

Assignee：`odoo-hedge-qa`

复用：`hedge-ci-watch-repair-loop`

输出：

- CI run URL
- pass / fail
- failure bucket
- repair task 或 green handoff

### PR Comments Follow-up

Assignee：`odoo-hedge-coder`

复用：`gh-pr-review-followup`

输出：

- comments classification
- confirmed adopt set
- fixes
- follow-up comment
- remaining unresolved comments

### Closeout Report

Assignee：`odoo-hedge-orchestrator`

复用：`issue-closeout-sync` 的事实同步规则。

输出：

- issue URL
- PR URL
- branch / worktree
- commits
- local tests
- CI status
- review comments status
- remaining risks
- user next action

## 失败回路

```text
Design Review REQUEST_CHANGES
  -> Architect Repair
  -> Design Review

Spec Review REQUEST_CHANGES
  -> Implementation Repair
  -> Spec Review

QA failed
  -> Implementation Repair
  -> Spec Review
  -> QA

CI failed
  -> Implementation Repair
  -> Spec Review
  -> QA
  -> CI Watch

PR comments actionable
  -> user confirms adopt set
  -> Implementation Repair
  -> Spec Review
  -> QA
  -> CI Watch
  -> PR Comments Follow-up
```

## V1 验收标准

V1 成功标准：

- 已有 issue 可以作为 root delivery task 输入。
- Orchestrator 默认从 `Bootstrap Worktree` 开始。
- worktree 在非 `master` branch 创建。
- 后续步骤复用 `odoo-hedge/.codex/skills`，不重复实现。
- 过程在 Kanban dashboard / CLI 中可见。
- human gate 可以阻塞并等待用户确认。
- 最终输出 closeout report。

## 下一步实施

1. 更新 `odoo-hedge-orchestrator` skill，使默认入口从已有 issue 的
   `Bootstrap Worktree` 开始。
2. 补齐 Architect / Reviewer / QA 的轻量 role skills。
3. 用一个已有低风险 issue 验证 issue -> worktree -> plan。
4. 再逐步接 implementation、PR、CI、comments loop。
