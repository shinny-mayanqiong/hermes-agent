# `odoo-hedge-workflow` Plugin Runbook

## 用途

`odoo-hedge-workflow` 是 `odoo-hedge` 开发专用 Hermes plugin command，
用于创建和推进 dynamic DAG workflow。

当前 plugin 已按 V2 skill-based workflow 实现。目标流程以
`../plans/2026-06-18-skill-based-delivery-workflow-v2.md` 为准：

```text
spec discussion -> spec freeze -> blueprint -> spec/blueprint review
-> implementation -> CI -> local code review -> PR comments -> closeout
```

本 runbook 记录当前命令的实际行为。

该 plugin 位于 Hermes 仓库：

```text
plugins/odoo-hedge-workflow/
```

它不是 Hermes 内置命令；需要在 profile 的 `plugins.enabled` 中启用。

当前已启用 profiles：

- `odoo-hedge-dev`
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
- 第一阶段 `spec_discussion` child task
- workflow 使用的 issue worktree

root task 使用 sticky `blocked` 状态作为 controller 状态容器，避免被
specifier / decomposer / dispatcher 当成普通 worker task 处理。root task
只由 `odoo-hedge-workflow tick` 更新。

示例：

```bash
hermes -p odoo-hedge-orchestrator odoo-hedge-workflow start --issue 955
```

worktree 解析规则：

1. 如果传入 `--worktree`，使用该路径并验证它是 git worktree。
2. 如果未传入，先在 `/home/user/Repos/odoo-hedge-worktrees` 查找匹配
   `issue-<num>` 的已有 worktree。
3. 如果没有匹配项，调用 `/home/user/Repos/odoo-hedge/scripts/codex-worktree.sh`
   创建新 worktree。该行为遵循 `issue-worktree-bootstrap` skill 的约定。

新建 worktree 的 branch topic 规则：

1. `--branch` 最高优先级，完整控制 branch。
2. `--topic` 次优先级，生成 `codex/issue-<number>-<topic>`。
3. 未传 `--topic` 时，尝试读取 GitHub issue body 的 `## Topic`，生成
   `codex/issue-<number>-<topic>`。
4. 如果没有 topic，fallback 为 `codex/issue-<number>`，不从中文 title 自动推导。

如果已经知道具体 worktree，可以显式传入：

```bash
hermes -p odoo-hedge-orchestrator odoo-hedge-workflow start \
  --issue 955 \
  --worktree /home/user/Repos/odoo-hedge-worktrees/codex-issue-955-soft-delete-futures-accounts
```

如果要控制新 worktree 的 branch：

```bash
hermes -p odoo-hedge-orchestrator odoo-hedge-workflow start \
  --issue 955 \
  --branch codex/issue-955-soft-delete-futures-accounts
```

如果只控制 topic：

```bash
hermes -p odoo-hedge-orchestrator odoo-hedge-workflow start \
  --issue 955 \
  --topic soft-delete-futures-accounts
```

如果只想检查是否存在 worktree，不允许自动创建：

```bash
hermes -p odoo-hedge-orchestrator odoo-hedge-workflow start \
  --issue 955 \
  --no-create-worktree
```

root task 保持 `blocked`，只由 `odoo-hedge-workflow tick` 读取和更新；
不要 unblock / promote root task。root/child task 的 workspace 必须是
worktree，不是主 repo 根目录。
task metadata 会记录 branch、worktree、`ODOO_DB_NAME`、test DB、
`PYTHON_VENV_PATH`、`ODOO_CONFIG`、http/gevent ports。

## `tick`

`tick` 表示推进一次 workflow 状态机。它不同于 Hermes 的
`kanban dispatch`：

- `kanban dispatch`：把 `ready` task 分配给 worker。
- `odoo-hedge-workflow tick`：读取 child task 结果并决定下一阶段。

`tick --plan` 只输出下一步计划，不创建 task。

`tick --apply` 创建下一阶段 child task，并写 root comment。

review gate 的 task result 必须包含结构化布尔字段：

- `spec_blueprint_review`：`"approved": true/false`
- `local_code_review`：`"approved": true/false`

plugin 会兼容 `review_decision=approved_with_non_blocking_notes` 这类常见
review 输出，但不能依赖 LLM 每次都严格遵守格式；缺少必需字段时，`tick`
不会再把 child 标记为 processed，方便回填 result 后重新推进。

review prompt 约束：

- `spec_blueprint_review` 是开发前文档 review。它必须判断 spec 是否清楚描述
  需求、范围、非范围、验收标准和风险边界；同时判断 blueprint 的阶段划分、
  执行顺序、验证方式和异常处理是否足以支撑后续 implementation 直接执行。
  如果涉及用户可见变化，还必须检查 UI/UX impact 是否写清楚；如果没有可见
  UI/UX 变化，也必须明确说明。
  如果需求定义不清、步骤缺失、验收不可执行或与 `odoo-hedge` repo 约束冲突，
  必须 `approved=false`，并设置 `return_phase=spec_freeze` 或
  `return_phase=blueprint_prompts`。
- `local_code_review` 是开发完成且 CI 通过后的本地 code review。它必须说明
  PR 解决了什么问题、通过哪些代码和测试改动解决；分析需求是否合理、实现方案
  是否合理、UI/UX impact 是什么、是否可以 approve，并给出 blocking /
  non-blocking 改进建议。review 结论必须直接发送为 PR comment，comment 中必须
  包含 UI/UX impact，并在 result 中记录 `pr_comment_url`。
- `closeout_sync` 只同步 closeout evidence、issue、Project 8 和文档状态。
  它禁止 merge PR、close PR 或 reopen PR。PR 未 merge 时不得 close issue，
  不得把 Project 8 移动到 `Done`；只能保持/设置为 `In Review` 并记录 blockers。
  PR 已 merge 且验收、风险/回滚、closeout 证据完整时，才可以关闭 issue 并把
  Project 8 移动到 `Done`。

## V2 phase map

主流程：

```text
start
-> spec_discussion
-> spec_freeze
-> blueprint_prompts
-> spec_blueprint_review
-> implementation
-> ci_watch_repair
-> local_code_review
-> pr_review_followup
-> closeout_sync
```

动态分支：

```text
spec_blueprint_review approved=false -> spec_freeze 或 blueprint_prompts
ci_watch_repair success=false -> implementation
ci_watch_repair success=true 且 mergeable=CONFLICTING/DIRTY/UNKNOWN -> branch_sync_repair
branch_sync_repair success=true -> ci_watch_repair
branch_sync_repair success=false 且 retry_branch_sync=true -> branch_sync_repair vN+1
branch_sync_repair success=false -> implementation
local_code_review approved=false -> implementation
pr_review_followup comments_resolved=false -> implementation
requires_i18n=true -> i18n_check -> ci_watch_repair
needs_followup_issue=true -> project_followup -> return_phase 或 implementation
needs_user_input=true -> 不创建下一阶段，等待用户输入
```

## execution backend

child task metadata 记录 `execution_backend`：

- `hermes_worker`：由 Kanban dispatcher 启动普通 Hermes worker。
- `codex_exec`：由 `odoo-hedge-workflow` spawn override 启动 wrapper，再在
  issue worktree 内执行 `codex exec`。

当前使用 `codex_exec` 的 phase：

- `implementation`
- `ci_watch_repair`
- `branch_sync_repair`
- `local_code_review`
- `pr_review_followup`
- `closeout_sync`

`codex_exec` worker 约束：

- `cwd` 必须是 issue worktree，不是 `/home/user/Repos/odoo-hedge` 主 repo。
- prompt 会引用 phase、issue、worktree、branch、skill 和 artifacts。
- stdout / stderr 写入 Kanban worker log。
- exit code 非 0 或缺少 summary JSON 时，task 进入 `blocked`。
- 成功时把最后回复中的 JSON 写回 task result / metadata。
- `codex exec` 不创建下一阶段 task；下一阶段只能由 `tick` 创建。

## 验证记录

已用临时 `HERMES_KANBAN_HOME` 验证：

- plugin command 可被 `odoo-hedge-dev` 和 `odoo-hedge-orchestrator` 发现。
- `start` 可创建 root task 和 `spec_discussion` child task。
- root task 保持 sticky `blocked`，不会被 specifier / decomposer /
  dispatcher 领取。
- `tick --plan` 在没有 completed child 时返回 wait。
- `tick --apply` 可从 `spec_discussion` 推进到 `implementation`。
- `implementation` child metadata 为 `execution_backend=codex_exec`、
  `skill=hedge-issue-delivery-loop`。
- fake `codex exec` worker 可由 dispatcher spawn，并把 task 标记为 done。
