# Skill-based Delivery Workflow V2

## 目标

把 `odoo-hedge` 开发 workflow 从早期的通用角色阶段
`architect/design/coder/qa`，调整为贴近现有 Codex skills 的交付流程。

V2 的核心原则：

- profile 表示角色和权限边界。
- skill 表示该角色执行某一步时必须遵守的流程能力。
- orchestrator 只负责任务编排、状态推进和通知，不直接讨论 spec、不改代码、
  不处理 PR。
- spec / blueprint / implementation / CI / review / closeout 都通过明确的
  Kanban child task 交接。
- review gate 分为两类：开发前 review 文档，开发后 review 代码。

## 非目标

V2 不做：

- 自动创建 GitHub issue 作为默认入口。
- 自动 merge PR。
- 绕过人工确认。
- 把 `odoo-hedge` repo 中的 Codex skills 全量重写到 Hermes。
- 为每个 skill 创建一个 profile。

## 入口

默认入口仍是已有 GitHub issue。

```bash
hermes -p odoo-hedge-orchestrator odoo-hedge-workflow start --issue <number>
```

`start` 必须先解析或创建 issue worktree。worktree contract 仍遵循
`issue-worktree-bootstrap` skill 和
`/home/user/Repos/odoo-hedge/scripts/codex-worktree.sh`。

如果没有 GitHub issue，流程阻塞等待用户确认；创建 issue 不属于默认 graph。

## Profiles

V2 推荐 profiles：

| Profile | 角色 | 主要 skills | 是否改代码 |
|---|---|---|---|
| `odoo-hedge-orchestrator` | workflow controller / 主持人 | 无固定开发 skill；执行 `start` / `tick` / `status` | 否 |
| `odoo-hedge-spec` | 需求讨论、spec 冻结、blueprint 工件 | `brainstorm-spec`, `spec-freeze`, `spec-blueprint-prompts` | 否 |
| `odoo-hedge-spec-reviewer` | review spec 和 blueprint | 可复用 review prompt / repo rules | 否 |
| `odoo-hedge-coder` | 实施开发 | `hedge-issue-delivery-loop` | 是 |
| `odoo-hedge-ci` | CI 盯盘和修复循环 | `hedge-ci-watch-repair-loop` | 是，仅限 CI / test 修复 |
| `odoo-hedge-code-reviewer` | 本地 code review | code review prompt / repo rules | 否，默认只输出 review |
| `odoo-hedge-pr-reviewer` | GitHub PR comments 处理 | `gh-pr-review-followup` | 视 comment 需要 |
| `odoo-hedge-closeout` | PR / issue / Project 收口 | `issue-closeout-sync` | 否，除非 closeout skill 明确要求 |
| `odoo-hedge-i18n` | 条件触发：中文翻译 | `translate-hedge-zh-cn` | 是，仅限 i18n |
| `odoo-hedge-project` | 条件触发：Project / follow-up issue | `gh-project-task-status` | 否 |

不建议把 profile 设计成 skill 的一一映射。一个 profile 可以在不同 task 中加载
不同 skills，但 profile 的权限边界必须清楚。

## 主流程

```text
root workflow
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

阶段与 profile 映射：

| Phase | Assignee profile | Required skills | 主要输入 | 主要输出 |
|---|---|---|---|---|
| `spec_discussion` | `odoo-hedge-spec` | `brainstorm-spec` | GitHub issue、用户反馈、repo docs | 需求澄清记录、开放问题 |
| `spec_freeze` | `odoo-hedge-spec` | `spec-freeze` | discussion 结果 | frozen spec 文档 |
| `blueprint_prompts` | `odoo-hedge-spec` | `spec-blueprint-prompts` | frozen spec | implementation blueprint / prompts / artifact list |
| `spec_blueprint_review` | `odoo-hedge-spec-reviewer` | review prompt / repo rules | frozen spec、blueprint | approve / request changes |
| `implementation` | `odoo-hedge-coder` | `hedge-issue-delivery-loop` | approved spec、blueprint、worktree | code changes、tests、commit、PR |
| `ci_watch_repair` | `odoo-hedge-ci` | `hedge-ci-watch-repair-loop` | PR、CI runs | CI pass/fail、修复 commits |
| `branch_sync_repair` | `odoo-hedge-ci` | `hedge-ci-watch-repair-loop` | PR mergeability、base branch、CI result | branch sync / rebase、conflict repair、push |
| `local_code_review` | `odoo-hedge-code-reviewer` | code review prompt / repo rules | diff、spec、CI 结果 | approve / request changes |
| `pr_review_followup` | `odoo-hedge-pr-reviewer` | `gh-pr-review-followup` | PR comments | comments 处理结果 |
| `closeout_sync` | `odoo-hedge-closeout` | `issue-closeout-sync` | merged/ready PR、issue、Project | closeout report |

## Codex Exec Worker Backend

V2 可以把部分 worker phase 实现为在 issue worktree 目录下执行
`codex exec <prompt>`。这不是替代 Hermes workflow，而是作为 worker 的执行后端。

职责边界：

- Hermes orchestrator 负责创建 Kanban child task、设置 cwd / worktree、
  记录 metadata、推进 `tick`、处理 gate 和通知。
- `codex exec` 负责在指定 worktree 中执行单个 phase 的实际工作。
- Kanban DB 仍是过程状态事实源。
- `odoo-hedge` repo 中的 `AGENTS.md`、`.codex/skills`、`hedge_docs/`
  仍是项目开发规则和业务 artifact 事实源。

推荐执行形态：

```bash
cd /home/user/Repos/odoo-hedge-worktrees/codex-issue-955-soft-delete-futures-accounts

codex exec "
使用 .codex/skills/hedge-issue-delivery-loop 完成 Issue #955。
输入 spec: hedge_docs/tasks/issue-955/spec.md
输入 blueprint: hedge_docs/tasks/issue-955/blueprint.md
完成后提交 commit，并输出 JSON summary。
"
```

适合使用 `codex exec` 的 phase：

- `implementation`
- `ci_watch_repair`
- `branch_sync_repair`
- `local_code_review`
- `pr_review_followup`
- `closeout_sync`

不适合只用 `codex exec` 的 phase：

- `spec_discussion`：需要和用户多轮交互。
- 任何需要 Slack / dashboard / CLI 人工确认的 gate。
- orchestrator 的 `tick` 决策。

`codex exec` worker contract：

1. 必须在 issue worktree 目录下运行，不能在主 repo 根目录运行。
2. prompt 必须引用当前 phase、issue、spec、blueprint、worktree、PR 等输入。
3. stdout / stderr 必须保存到 Kanban run log。
4. exit code 非 0 时，task 进入 `blocked` 或 `failed/retry` 路径。
5. 成功时必须把 summary JSON 写回 Kanban task summary/result。
6. 如果产生或修改 artifact，summary JSON 必须列出 artifact path。
7. 如果需要用户输入，summary JSON 必须设置 `needs_user_input=true`。

Hermes plugin 后续可以支持一种 phase execution mode：

```yaml
phase: implementation
assignee: odoo-hedge-coder
execution_backend: codex_exec
cwd: /home/user/Repos/odoo-hedge-worktrees/codex-issue-955-soft-delete-futures-accounts
skill: hedge-issue-delivery-loop
prompt_artifacts:
  - hedge_docs/tasks/issue-955/spec.md
  - hedge_docs/tasks/issue-955/blueprint.md
```

该模式仍然由 Hermes dispatcher 领取 Kanban task。worker 启动后再调用
`codex exec`，并把执行结果回写到 Kanban。不要让 `codex exec` 自己负责
创建下一阶段 task 或推进 DAG。

## Review Gates

### Spec / Blueprint Review

`spec_blueprint_review` 在开发前执行。

review 对象：

- frozen spec
- blueprint prompts
- artifact list
- acceptance criteria
- test plan
- worktree / branch / issue metadata

必须检查：

- scope 是否清楚。
- acceptance criteria 是否可验证。
- Odoo model / security / migration / i18n / test 边界是否明确。
- blueprint 是否足够让 coder 直接执行。
- 是否需要拆 follow-up issue。
- 是否存在未解决的人类决策点。

不通过时回到：

```text
spec_freeze
```

或：

```text
blueprint_prompts
```

由 orchestrator 根据 reviewer 的结构化 summary 决定。

### Local Code Review

`local_code_review` 在 `implementation` 和 `ci_watch_repair` 通过后执行。

review 对象：

- PR diff / local diff
- frozen spec
- blueprint
- CI 结果
- test evidence
- translation artifacts

必须检查：

- 实现是否符合 spec。
- 是否引入未声明的行为变化。
- 测试是否覆盖核心 acceptance criteria。
- Odoo ORM / security / migration / data / view 约束是否合理。
- 变量命名、业务命名、中文翻译是否符合项目习惯。
- 是否需要 `translate-hedge-zh-cn`。
- 是否需要拆 follow-up issue。

不通过时回到：

```text
implementation
```

必要时再进入：

```text
ci_watch_repair
```

通过后才进入 `pr_review_followup`。

## 条件分支

### i18n

如果任一阶段发现新增或修改可见 UI 文案：

```text
implementation or local_code_review
  -> i18n_check
  -> ci_watch_repair
```

`i18n_check` 使用：

```text
profile: odoo-hedge-i18n
skill: translate-hedge-zh-cn
```

### Follow-up Issue

如果 spec review、implementation、local code review 或 PR comments 发现需要拆
follow-up issue：

```text
any_phase.needs_followup_issue=true
  -> project_followup
  -> return to original next phase
```

`project_followup` 使用：

```text
profile: odoo-hedge-project
skill: gh-project-task-status
```

follow-up issue 只记录后续工作，不应扩大当前 issue 的 scope，除非用户明确确认。

## 状态转换

```text
root created
  -> spec_discussion

spec_discussion.done
  -> spec_freeze

spec_freeze.done
  -> blueprint_prompts

blueprint_prompts.done
  -> spec_blueprint_review

spec_blueprint_review.approved=true
  -> implementation

spec_blueprint_review.approved=false
  -> spec_freeze or blueprint_prompts

implementation.done
  -> ci_watch_repair

ci_watch_repair.success=false
  -> implementation

ci_watch_repair.success=true and mergeable clean
  -> local_code_review

ci_watch_repair.success=true and mergeable conflicting/dirty/unknown
  -> branch_sync_repair

branch_sync_repair.success=true
  -> ci_watch_repair

branch_sync_repair.success=false
  -> implementation

local_code_review.approved=false
  -> implementation

local_code_review.approved=true
  -> pr_review_followup

pr_review_followup.comments_resolved=false
  -> implementation

pr_review_followup.comments_resolved=true
  -> closeout_sync

closeout_sync.done
  -> done or blocked_for_user
```

## Summary JSON Contract

每个 child task 的 summary 必须包含 JSON object。正文可以有人类说明，但
orchestrator 读取 JSON 作为主接口。

通用字段：

```json
{
  "workflow_id": "issue-955",
  "phase": "spec_discussion",
  "iteration": 1,
  "status": "done",
  "artifacts": [],
  "blockers": [],
  "needs_user_input": false,
  "needs_followup_issue": false,
  "next_recommended_phase": "spec_freeze"
}
```

### `spec_blueprint_review`

```json
{
  "workflow_id": "issue-955",
  "phase": "spec_blueprint_review",
  "iteration": 1,
  "approved": false,
  "blocking_comments": [
    "acceptance criteria do not specify expected behavior for archived records"
  ],
  "non_blocking_comments": [],
  "return_phase": "spec_freeze",
  "next_recommended_phase": "spec_freeze"
}
```

通过时：

```json
{
  "workflow_id": "issue-955",
  "phase": "spec_blueprint_review",
  "iteration": 1,
  "approved": true,
  "blocking_comments": [],
  "next_recommended_phase": "implementation"
}
```

### `local_code_review`

```json
{
  "workflow_id": "issue-955",
  "phase": "local_code_review",
  "iteration": 1,
  "approved": false,
  "blocking_comments": [
    "implementation does not cover the spec acceptance criterion for callback cleanup"
  ],
  "requires_i18n": false,
  "requires_followup_issue": false,
  "next_recommended_phase": "implementation"
}
```

通过时：

```json
{
  "workflow_id": "issue-955",
  "phase": "local_code_review",
  "iteration": 1,
  "approved": true,
  "blocking_comments": [],
  "next_recommended_phase": "pr_review_followup"
}
```

## Artifact Contract

V2 中普通业务任务的 spec、blueprint、实现记录应写入 `odoo-hedge` 仓库自己的
事实源，而不是写入 Hermes docs。

推荐位置：

```text
/home/user/Repos/odoo-hedge/hedge_docs/tasks/issue-<number>/
```

最小 artifact 集：

```text
spec.md
blueprint.md
spec-blueprint-review.md
implementation-report.md
ci-report.md
local-code-review.md
pr-review-followup.md
closeout.md
```

如果 task 较小，可以合并报告文件，但 summary JSON 必须指向实际 artifact path。

## Plugin 实现状态

`odoo-hedge-workflow` plugin 已按 V2 接入：

1. phase 列表和 phase -> profile 映射已更新为 V2。
2. child task metadata 记录 per-phase `skill`。
3. child task metadata 记录 per-phase `execution_backend`。
4. `start` 第一阶段为 `spec_discussion`。
5. `tick` 根据 V2 summary JSON 决定下一阶段。
6. 已支持 `spec_blueprint_review` 和 `local_code_review` 两个 review gate。
7. 已支持 `i18n_check` 和 `project_followup` 条件分支。
8. root task 保持 sticky `blocked` controller 约定，避免被 dispatcher 领取。
9. 保留 issue worktree resolve / create 约定。
10. `codex_exec` backend 通过 Kanban spawn override 接入 dispatcher；wrapper
    在 issue worktree 内执行 `codex exec` 并把 summary JSON 回写到 task。

## 当前决策

- 采用 V2 skill-based workflow 作为后续目标流程。
- 不再把 `architect_design -> design_review` 作为主流程。
- 允许部分 phase 使用 `codex exec` 作为 worker 执行后端，但 Hermes 仍负责
  Kanban 编排、状态记录、gate 和 `tick`。
- 保留旧 V1 文档作为历史与当前 plugin 状态说明。
- 下一步创建缺失 profiles，并在真实 issue 上执行端到端 dry run。
