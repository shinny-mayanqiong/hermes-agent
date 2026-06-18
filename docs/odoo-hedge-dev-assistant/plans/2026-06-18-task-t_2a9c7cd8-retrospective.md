# Task `t_2a9c7cd8` 复盘

## 背景

Task `t_2a9c7cd8` 由 dashboard 创建，目标是继续完成 issue `#955`
相关工作：

```text
issues #955 ，soft delete future account 已经创建了 worktree, 继续完成剩余的工作
```

该 task 最终创建了 PR：

```text
https://github.com/shinnytech/odoo-hedge/pull/990
```

但该 task 没有经过预期的多角色流程，而是基本由单个
`odoo-hedge-architect` profile 完成了分析、开发、测试、提交、PR 创建和
CI 观察。

## 实际运行时间线

### 创建 task

- `2026-06-16 11:02`：dashboard 创建 task。
- assignee：`odoo-hedge-architect`。
- 该 task 是普通 Kanban task，不是 workflow root task。

### 前两次运行失败

- run #9：`2026-06-16 11:04`，crashed。
- run #10：`2026-06-16 11:05`，crashed。
- 失败原因：`openai-codex` OAuth 返回 HTTP 401 / `token_expired`。

这两次没有进入有效开发。

### run #11 完成实际开发

- `2026-06-16 11:35`：修复 Codex auth 后 unblock。
- run #11 由 `odoo-hedge-architect` 执行。
- 该 run 做了以下事情：
  - 读取 issue `#955`。
  - 读取 `odoo-hedge` 文档和相关源码。
  - 修改 futures account model、view 和 tests。
  - 执行 targeted tests。
  - commit 并 push branch。

产出：

```text
branch: codex/issue-955-soft-delete-futures-accounts
commit: da1db2eb4 [FIX] hedge: soft delete futures accounts
```

验证记录：

- `ruff check` passed。
- `_test` DB install passed。
- targeted futures account soft-delete tests passed。
- futures account view tests passed。
- `python scripts/ci_check.py` 因缺少 `ast-grep` 失败，但其他检查通过。

### run #11 block

run #11 创建 PR 时失败：

```text
gh pr create failed because GitHub CLI is not authenticated in this worker environment.
```

task 被标记为 `blocked`，并写入 handoff。

### 修复 worker GitHub CLI auth

- `2026-06-18 10:45`：把 GitHub CLI 配置安装到各
  `odoo-hedge-*` profile 的隔离 HOME。
- 原因：Hermes worker 使用
  `~/.hermes/profiles/<profile>/home/` 作为 `$HOME`，不会读取用户主目录的
  `~/.config/gh/hosts.yml`。
- `GH_TOKEN` 在 Hermes terminal 子进程中属于安全 blocklist，不能作为
  `env_passthrough` 方案。

### run #12 创建 PR

- `2026-06-18 10:48`：promote 后重新运行。
- run #12 验证 worker 内 `gh auth status` 可用。
- 确认 branch 没有已有 PR。
- 创建 PR `#990`：

```text
https://github.com/shinnytech/odoo-hedge/pull/990
```

### run #12 观察 CI

run #12 使用 `gh pr checks 990 --watch --interval 10` 观察 CI。

记录结果：

- passed：
  - Detect Changes
  - Ruff Linter
  - Code Quality Checks
  - Module Install Test
  - Demo Data Smoke
- skipped：
  - Daily UI Audit Tooling Checks
  - Exploratory UI Audit Tooling Checks
  - Hedge CLI Test
  - Playwright Tooling Checks
  - TD Gateway Test
  - Upload to OSS

GitHub 状态：

```text
REVIEW_REQUIRED / mergeStateStatus BLOCKED
```

### 最终状态

- `t_2a9c7cd8` 标记为 `done`。
- PR 已创建并等待人工 review。

## 不理想之处

### 没有进入多角色流程

这次 task 被直接 assign 给 `odoo-hedge-architect`，因此 dispatcher 只启动了
单个 architect worker。

预期多角色流程应该是：

```text
orchestrator
-> architect
-> design reviewer
-> coder
-> spec reviewer
-> qa
-> pr / ci / review follow-up
```

实际流程是：

```text
architect
-> code changes
-> tests
-> commit / push
-> PR
-> CI watch
```

### Architect profile 越权

`odoo-hedge-architect` 本应负责：

- 理解 issue。
- 产出设计。
- 拆分阶段。
- 明确验收标准。

但实际执行了：

- 修改业务代码。
- 修改测试。
- commit / push。
- 创建 PR。

这说明当前 profile skill 只是软约束，不能防止角色越权。

### Dashboard 普通 task 绕过 orchestrator

dashboard 创建的是普通 Kanban task。只要 assignee 是某个 worker profile，
dispatcher 就会直接启动该 worker。

当前没有机制强制：

- 开发需求必须先进入 `odoo-hedge-orchestrator`。
- worker task 必须由 orchestrator 创建。
- worker task 必须带 workflow metadata。

### 没有 workflow root task

`t_2a9c7cd8` 自身承担了需求入口、开发执行和 PR 结果记录三种角色。

它不是一个 workflow root task，因此没有稳定位置记录：

- workflow id
- current phase
- iteration
- active child task
- reviewer verdict
- pending user input

### 没有阶段结果 schema

run #11 的 handoff 是人工可读 JSON，但不是强约束 schema。

缺少统一字段，例如：

- `phase`
- `iteration`
- `approved`
- `pass`
- `blocking_comments`
- `next_recommended_phase`
- `artifacts`

因此 orchestrator 无法可靠、机械地根据结果创建下一步 task。

### 缺少 role guard

当前没有 guard 阻止：

- architect / reviewer 修改业务代码。
- 普通 task 直接 assign 给 coder / qa。
- 未经 design review 就进入 coder。
- 未经 spec review / QA 就创建 PR。

## 根因

根因不是 Kanban dispatcher 失效，而是入口和流程约束没有实现。

Kanban dispatcher 的行为是正确的：它看到了一个 `ready` task，assignee 是
`odoo-hedge-architect`，于是启动了对应 worker。

缺失的是：

- 固定 workflow 入口。
- 动态 DAG controller。
- 子任务 metadata。
- role guard。
- reviewer 输出 schema。

## 改进方向

### 使用动态 DAG

不要要求 root task 一次性创建所有子任务。orchestrator 应在每个阶段完成后
读取结果，再决定下一步。

示例：

```text
architect_design v1
-> design_review v1

如果 approved=false:
  -> architect_revise v2
  -> design_review v2

如果 approved=true:
  -> coder_implement v1
  -> spec_review v1
```

### 引入 orchestrator tick

新增一个最小 controller 行为：

```text
orchestrator tick(root_task_id)
```

每次 tick：

1. 读取 root task 状态。
2. 找到最近完成且未处理的 child task。
3. 读取 child summary。
4. 根据 phase 状态机决定下一步。
5. 创建新的 child task。
6. link dependency。
7. 在 root task 写 comment 记录决策。

### 引入 worker task metadata

每个 child task body 必须包含：

```yaml
workflow_id: issue-955
root_task_id: t_xxx
phase: architect_design
iteration: 1
allowed_actions:
  - read
  - write_design_doc
forbidden_actions:
  - edit_business_code
  - git_commit
expected_output_schema: ...
```

### 引入 role guard

短期可以先用 wrapper / runbook 约束；中期应做 Hermes plugin 或本地
workflow command。

guard 规则：

- 普通开发需求只能创建 root task，assignee 固定为
  `odoo-hedge-orchestrator`。
- worker task 必须带 `workflow_id`、`phase`、`iteration`。
- architect / reviewer task 不允许产生业务代码 diff。
- coder task 必须依赖 approved design review。
- PR task 必须依赖 spec review 和 QA pass。

## 后续文档

动态 DAG 方案详见：

```text
docs/odoo-hedge-dev-assistant/plans/2026-06-18-dynamic-dag-workflow-v1.md
```
