# `odoo-hedge-workflow` Plugin Runbook

## 用途

`odoo-hedge-workflow` 是 `odoo-hedge` 开发专用 Hermes plugin command，
用于创建和推进 dynamic DAG workflow。

该 plugin 位于 Hermes 仓库：

```text
plugins/odoo-hedge-workflow/
```

它不是 Hermes 内置命令；需要在 profile 的 `plugins.enabled` 中启用。

当前已启用 profiles：

- `odoo-hedge-dev`
- `odoo-hedge-orchestrator`

## 命令

```bash
hermes -p odoo-hedge-orchestrator odoo-hedge-workflow start --issue 955
hermes -p odoo-hedge-orchestrator odoo-hedge-workflow tick <root_task_id> --plan
hermes -p odoo-hedge-orchestrator odoo-hedge-workflow tick <root_task_id> --apply
hermes -p odoo-hedge-orchestrator odoo-hedge-workflow status <root_task_id>
```

默认 Kanban board：

```text
odoo-hedge-dev
```

可用 `--board <slug>` 覆盖。

## `start`

`start` 创建：

- workflow root task
- 第一阶段 `architect_design` child task

root task 使用 `triage` 状态作为 controller 状态容器，避免被 dispatcher
当成普通 worker task 领取。

示例：

```bash
hermes -p odoo-hedge-orchestrator odoo-hedge-workflow start \
  --issue 955 \
  --worktree /home/user/Repos/odoo-hedge-worktrees/codex-issue-955-soft-delete-futures-accounts
```

## `tick`

`tick` 表示推进一次 workflow 状态机。它不同于 Hermes 的
`kanban dispatch`：

- `kanban dispatch`：把 `ready` task 分配给 worker。
- `odoo-hedge-workflow tick`：读取 child task 结果并决定下一阶段。

`tick --plan` 只输出下一步计划，不创建 task。

`tick --apply` 创建下一阶段 child task，并写 root comment。

## V1 支持的动态分支

当前 V1 已支持：

```text
start
-> architect_design
-> design_review

design_review approved=false
-> architect_revise v2

design_review approved=true
-> coder_implement
```

同时保留后续阶段的状态机：

```text
coder_implement -> spec_review
spec_review pass=false -> coder_fix
spec_review pass=true -> qa_verify
qa_verify pass=false -> coder_fix
qa_verify pass=true -> pr_ci
```

## 验证记录

已用临时 `HERMES_KANBAN_HOME` 验证：

- plugin command 可被 `odoo-hedge-dev` 和 `odoo-hedge-orchestrator` 发现。
- `start` 可创建 root task 和 `architect_design` child task。
- root task 保持 `triage`，不会被 dispatcher 领取。
- `tick --plan` 在没有 completed child 时返回 wait。
- `tick --apply` 可从 completed `architect_design` 创建 `design_review`。
- `design_review approved=false` 后，`tick --apply` 可创建
  `architect_revise v2`。
