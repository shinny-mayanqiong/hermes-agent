# Dynamic DAG Workflow V1 草案

## 目标

为 `odoo-hedge` 开发任务定义一个动态 DAG workflow，使 Hermes 能以多角色
agents 协作的方式推进需求，而不是让单个 worker profile 一次性完成所有工作。

V1 目标：

- 所有开发需求先进入 `odoo-hedge-orchestrator`。
- orchestrator 按阶段动态创建 child tasks。
- reviewer 不通过时创建新的修正 task，而不是复用或 reopen 旧 task。
- 每个阶段通过结构化 summary 交接。
- Kanban dispatcher 只负责调度 `ready` task，不负责业务决策。
- V1 直接接入 Hermes plugin CLI command，不改 Hermes core。

## 非目标

V1 不处理：

- 完整 GUI workflow builder。
- 所有 Hermes Kanban 的通用 workflow template 能力。
- 自动 merge PR。
- 自动绕过人工 review。
- 一次性创建完整静态 DAG。

## 核心模型

### root task

root task 是 workflow controller 的状态容器，不直接开发代码。

root task assignee 固定为：

```text
odoo-hedge-orchestrator
```

root task body 必须包含：

```yaml
workflow_type: odoo_hedge_dynamic_delivery_v1
workflow_id: issue-955
entry: existing_issue
issue: 955
repo: /home/user/Repos/odoo-hedge
worktree: /home/user/Repos/odoo-hedge-worktrees/codex-issue-955-soft-delete-futures-accounts
base_branch: master
current_phase: architect_design
iteration: 1
status: running
```

### child task

child task 是具体 worker profile 执行的最小单元。

child task body 必须包含：

```yaml
workflow_type: odoo_hedge_dynamic_delivery_v1
workflow_id: issue-955
root_task_id: t_root
phase: architect_design
iteration: 1
assignee_role: architect
input_artifacts:
  - issue: 955
  - worktree: /path/to/worktree
allowed_actions:
  - read_repo
  - inspect_issue
  - write_design_artifact
forbidden_actions:
  - edit_business_code
  - git_commit
  - git_push
expected_output_schema: architect_design_v1
```

### dependency

Kanban task dependency 表达“什么时候可以开始”。

示例：

```text
design_review_v1 depends_on architect_design_v1
coder_implement_v1 depends_on design_review_v1
```

orchestrator 动态创建新 task 时，应 link 到上一阶段 task。

## 阶段状态机

V1 固定阶段如下：

```text
architect_design
design_review
architect_revise
coder_implement
spec_review
coder_fix
qa_verify
pr_ci
done
blocked_for_user
```

状态转换：

```text
root created
  -> architect_design

architect_design.done
  -> design_review

design_review.approved=true
  -> coder_implement

design_review.approved=false
  -> architect_revise

architect_revise.done
  -> design_review(iteration + 1)

coder_implement.done
  -> spec_review

spec_review.pass=true
  -> qa_verify

spec_review.pass=false
  -> coder_fix

coder_fix.done
  -> spec_review(iteration + 1)

qa_verify.pass=true
  -> pr_ci

qa_verify.pass=false
  -> coder_fix

pr_ci.success=true
  -> done or blocked_for_user(review_required)

pr_ci.ci_failed=true
  -> coder_fix

any_phase.needs_user_input=true
  -> blocked_for_user
```

## 输出 schema

所有 child task 必须在 `summary` 中写入结构化 JSON。正文可以有人类说明，
但 JSON 是 orchestrator 读取的主接口。

### architect_design summary

```json
{
  "workflow_id": "issue-955",
  "phase": "architect_design",
  "iteration": 1,
  "status": "done",
  "design_artifacts": [
    "hedge_docs/tasks/issue-955/design.md"
  ],
  "implementation_scope": [
    "custom-addons/hedge/models/futures_base/account.py",
    "custom-addons/hedge/views/90_settings/account_views.xml"
  ],
  "acceptance_criteria": [
    "unlink archives futures account instead of hard deleting it",
    "archived account is hidden from normal active searches",
    "enabling archived account reactivates it"
  ],
  "risks": [
    "existing FK references must remain valid",
    "ZQ process shutdown must not run twice"
  ],
  "next_recommended_phase": "design_review"
}
```

### design_review summary

```json
{
  "workflow_id": "issue-955",
  "phase": "design_review",
  "iteration": 1,
  "approved": false,
  "blocking_comments": [
    "Design does not specify active_test=False access path for archived records.",
    "Design does not state whether disable write should stop ZQ process during unlink soft delete."
  ],
  "non_blocking_comments": [],
  "next_recommended_phase": "architect_revise"
}
```

如果通过：

```json
{
  "workflow_id": "issue-955",
  "phase": "design_review",
  "iteration": 2,
  "approved": true,
  "blocking_comments": [],
  "next_recommended_phase": "coder_implement"
}
```

### coder_implement summary

```json
{
  "workflow_id": "issue-955",
  "phase": "coder_implement",
  "iteration": 1,
  "status": "done",
  "branch": "codex/issue-955-soft-delete-futures-accounts",
  "commit": "da1db2eb4",
  "changed_files": [
    "custom-addons/hedge/models/futures_base/account.py",
    "custom-addons/hedge/views/90_settings/account_views.xml",
    "custom-addons/hedge/tests/test_futures_account_test_mode_policy.py"
  ],
  "tests_run": [
    {
      "command": "ruff check ...",
      "result": "passed"
    }
  ],
  "next_recommended_phase": "spec_review"
}
```

### spec_review summary

```json
{
  "workflow_id": "issue-955",
  "phase": "spec_review",
  "iteration": 1,
  "pass": false,
  "blocking_comments": [
    "Implementation archives records but does not verify normal search hides archived records."
  ],
  "next_recommended_phase": "coder_fix"
}
```

### qa_verify summary

```json
{
  "workflow_id": "issue-955",
  "phase": "qa_verify",
  "iteration": 1,
  "pass": true,
  "checks": [
    {
      "name": "targeted odoo tests",
      "result": "passed"
    },
    {
      "name": "translation/naming scan",
      "result": "passed"
    }
  ],
  "next_recommended_phase": "pr_ci"
}
```

### pr_ci summary

```json
{
  "workflow_id": "issue-955",
  "phase": "pr_ci",
  "iteration": 1,
  "success": true,
  "pr_number": 990,
  "pr_url": "https://github.com/shinnytech/odoo-hedge/pull/990",
  "ci_status": {
    "passed": [
      "Ruff Linter",
      "Code Quality Checks",
      "Module Install Test"
    ],
    "failed": [],
    "skipped": []
  },
  "review_required": true,
  "next_recommended_phase": "blocked_for_user"
}
```

## Hermes plugin command

`odoo-hedge-workflow` 是 V1 需要新增的 Hermes plugin command，不是 Hermes
当前已有的内置命令。

其中 `tick` 是本 plugin 自定义的 workflow controller command，表示“推进一次
workflow 状态机”。它不同于 Hermes 内置的 `kanban dispatch`：dispatcher
只负责把 `ready` task 分配给 worker，`tick` 负责读取 child task 结果并决定
下一阶段。

目标命令形态：

```bash
hermes -p odoo-hedge-orchestrator odoo-hedge-workflow start --issue 955
hermes -p odoo-hedge-orchestrator odoo-hedge-workflow tick <root_task_id>
hermes -p odoo-hedge-orchestrator odoo-hedge-workflow status <root_task_id>
```

V1 不先做 repo script 入口。可以把核心逻辑拆成 Python module 以便测试，
但用户和 dashboard / Slack 侧调用的正式入口应是 Hermes plugin command。

### start

`start` 创建 workflow root task，并创建第一步 `architect_design` child task。

职责：

1. 验证 issue / worktree / repo 参数。
2. 创建 root task，assignee 固定为 `odoo-hedge-orchestrator`。
3. 创建第一步 child task，assignee 为 `odoo-hedge-architect`。
4. link root / child dependency 或在 root comment 中记录 child id。
5. 输出 root task id 和第一步 child task id。

### tick

`tick` 是 V1 的核心 controller 行为：推进 workflow 到下一步。

### tick 输入

- `root_task_id`
- root task body
- root task comments
- child tasks
- child task results / summaries
- task dependency graph

### tick 行为

1. 验证 root task 是 `workflow_type: odoo_hedge_dynamic_delivery_v1`。
2. 找到当前 workflow 的所有 child tasks。
3. 找到最新完成、且 root 尚未处理过的 child task。
4. 解析 child summary JSON。
5. 根据阶段状态机决定下一阶段。
6. 创建下一阶段 child task。
7. link dependency：新 task depends_on 上一阶段 task。
8. 在 root task 写 comment，记录：
   - processed child task id
   - verdict
   - created next task id
   - next phase
9. 如果需要用户输入，把 root task 或 controller task block。

### tick 幂等性

tick 必须幂等。

同一个 child task summary 只能处理一次。root task comment 或 metadata 中需要记录：

```json
{
  "processed_child_tasks": [
    "t_architect_1",
    "t_design_review_1"
  ]
}
```

如果重复 tick，不应重复创建同一阶段 task。

### status

`status` 输出 workflow 当前状态：

- root task status
- current phase
- current iteration
- active child task
- last completed child task
- next expected action
- blocked reason
- PR / CI 状态

## Worker role policy

### orchestrator

允许：

- 创建 child tasks。
- link dependency。
- 读取 child results。
- block workflow 等待用户输入。
- 汇总 workflow 状态。

禁止：

- 修改业务代码。
- commit / push。

### architect

允许：

- 读取 issue、代码、文档。
- 产出设计 artifact。
- 定义实现边界和验收标准。

禁止：

- 修改业务代码。
- 修改测试。
- commit / push。
- 创建 PR。

### design reviewer

允许：

- 审查 architect design。
- 输出 `approved` / `blocking_comments`。

禁止：

- 修改业务代码。
- 直接改设计 artifact，除非 task 明确允许建议性 patch。
- commit / push。

### coder

允许：

- 修改业务代码。
- 修改测试。
- 运行本地验证。
- commit / push feature branch。

前置条件：

- 必须依赖一个 `design_review approved=true` task。

禁止：

- 跳过 design review。
- 创建 PR，除非 phase 明确是 `pr_ci`。

### spec reviewer

允许：

- 对照 approved design 审实现。
- 检查 diff 是否满足 spec。
- 输出 `pass` / `blocking_comments`。

禁止：

- 修改业务代码。
- commit / push。

### qa

允许：

- 运行测试。
- 检查命名、翻译、配置、常见回归风险。
- 输出 `pass` / `blocking_comments`。

禁止：

- 主动修改业务代码。
- commit / push。

### pr_ci

允许：

- 创建 PR。
- 观察 CI。
- 收集 PR comments。
- 根据 comments 创建后续 `coder_fix` 或 reviewer task。

禁止：

- merge PR。

## Guard 规则

V1 应实现至少以下 guard。短期可由 wrapper / script 检查；中期放入 Hermes
plugin 或 Kanban dispatch hook。

### 创建入口 guard

普通开发需求不能直接创建给 worker profile。

允许：

```text
assignee=odoo-hedge-orchestrator
workflow_type=odoo_hedge_dynamic_delivery_v1
```

阻止或提示：

```text
assignee=odoo-hedge-architect
assignee=odoo-hedge-coder
assignee=odoo-hedge-qa
```

除非 task body 明确包含合法 `workflow_id`、`root_task_id`、`phase`、
`iteration`，并且是 orchestrator 创建。

### role action guard

architect / reviewer / qa task 完成时，如果发现业务代码 diff 或 commit，应
block 并要求人工确认。

V1 可先通过 worker 自检实现：

```bash
git diff --name-only
git status --short
```

中期再通过 plugin hook 强制检查。

### phase dependency guard

`coder_implement` 必须依赖 `design_review approved=true`。

`pr_ci` 必须依赖：

- `spec_review pass=true`
- `qa_verify pass=true`

## Dashboard / Slack / CLI 入口

V1 需要统一入口，不再让用户直接创建任意 assignee 的开发 task。

推荐入口：

```bash
hermes -p odoo-hedge-orchestrator odoo-hedge-workflow start --issue 955
```

或者 dashboard 创建 root task 时使用模板：

```yaml
workflow_type: odoo_hedge_dynamic_delivery_v1
entry: existing_issue
issue: 955
```

assignee 固定为：

```text
odoo-hedge-orchestrator
```

## V1 实施步骤

### Step 1：文档和模板

- 完成本 spec。
- 创建 root task body template。
- 创建各 phase child task template。
- 更新 orchestrator skill，要求它只创建和推进 workflow，不直接开发。
- 更新 architect / reviewer / qa skills，明确 forbidden actions。

### Step 2：Hermes plugin scaffold

创建 project-specific Hermes plugin：

```text
~/.hermes/plugins/odoo-hedge-workflow/
```

plugin 注册 CLI command：

```bash
hermes odoo-hedge-workflow start ...
hermes odoo-hedge-workflow tick ...
hermes odoo-hedge-workflow status ...
```

该 plugin 是 `odoo-hedge` 项目专用入口，不进入 Hermes core。

### Step 3：核心 workflow library

实现可测试的 Python module：

```text
~/.hermes/plugins/odoo-hedge-workflow/workflow.py
```

职责：

- 读取 Kanban task。
- 解析 JSON summary。
- 创建下一阶段 task。
- link dependency。
- 写 root comment。

CLI command 只做参数解析和输出，核心判断放在 `workflow.py`，便于后续测试和
复用到 Slack / dashboard。

### Step 4：plugin CLI commands

实现：

- `start --issue <number> [--worktree <path>]`
- `tick <root_task_id> [--plan] [--apply]`
- `status <root_task_id> [--json]`

`tick --plan` 只输出下一步建议，不创建 task。

`tick --apply` 创建下一步 task、link dependency，并写 root comment。

### Step 5：guard

实现最小 guard：

- 检查 worker task metadata。
- 检查 role action。
- 对违规 task 自动 block。

V1 guard 可以先放在 plugin 的 `start` / `tick` 中；后续再扩展到 Kanban
dispatch hook。

### Step 6：Slack / dashboard 交互

把 start / tick / status 暴露到 Slack 或 dashboard。

## 开放问题

- root task 状态应写入 comments、task metadata，还是单独 artifact 文件？
- child summary JSON 应由 worker 直接写 `--summary`，还是写 artifact 后在
  summary 中引用？
- reviewer 不通过时，是否允许 reviewer 创建修正建议 patch？
- PR comments 应该由 `pr_ci` 直接创建 `coder_fix`，还是回到 orchestrator 决策？
- 是否需要单独 `ci-watcher` profile，还是由 `pr_ci` phase 复用
  `odoo-hedge-orchestrator`？

## 与 `t_2a9c7cd8` 复盘的关系

`t_2a9c7cd8` 暴露的问题是普通 task 可以直接 assign 给 worker profile，导致
architect 越权完成开发。

本 spec 的核心修复是：

- 固定 root task 入口。
- worker task 必须由 orchestrator 创建。
- child task 必须带 phase metadata。
- reviewer / QA 结果通过 JSON schema 驱动下一步。
- orchestrator 可动态创建返工和复审 task。
