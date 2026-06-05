# 2026-06-04 Delivery Workflow V1

## 目标

把 Hermes 作为 `odoo-hedge` 的交付编排层：用户提出一个需求后，系统可以
逐步完成 issue、worktree、规划、开发、测试、PR、CI、PR comments 处理和
最终汇报。

V1 目标不是一次性完全无人值守，而是建立一个稳定、可见、可审计、带人工
gate 的 delivery workflow。

## 架构原则

- Kanban 是 workflow 状态机和可视化层。
- GitHub issue / PR 是对外协作层。
- Git worktree 是代码执行隔离层。
- Hermes profiles 是执行身份。
- Skills 是每个角色的操作规程。
- Orchestrator 只编排，不直接写代码。
- 每一步都必须输出 `Latest summary`；重要结论必须写 Kanban comment。
- 高风险操作必须进入 human gate，不自动继续。

## 核心对象

### Delivery

一个用户需求对应一个 delivery。

字段：

- `delivery_id`
- `user_request`
- `root_task_id`
- `github_issue`
- `branch`
- `worktree_path`
- `pr_url`
- `current_phase`
- `blockers`
- `final_status`

V1 不新建数据库，先把这些字段写入 Kanban root task 的 body、metadata、
comments 和 child task summaries。

### Task

每个 workflow step 对应一个 Kanban task。

字段：

- `task_id`
- `role`
- `assignee_profile`
- `input_artifacts`
- `output_artifacts`
- `status`
- `human_gate_required`
- `summary`

### Artifact

交付过程中必须可追踪的产物：

- issue draft
- GitHub issue URL
- branch name
- worktree path
- architecture plan
- design review result
- changed files
- local verification commands and results
- PR URL
- CI run URL / status
- review comments classification
- closeout report

## 用户入口

用户通过 CLI、Slack 或 dashboard chat 提出需求。

V1 推荐 CLI 创建 root task：

```bash
cd /home/user/Repos/hermes-agent
source .venv/bin/activate

hermes -p odoo-hedge-dev kanban --board odoo-hedge-dev create \
  "Delivery：<一句话需求标题>" \
  --assignee odoo-hedge-orchestrator \
  --workspace scratch \
  --skill odoo-hedge-orchestrator \
  --body "<用户需求正文>"
```

后续 Slack 入口应生成同样的 root task，而不是另建独立流程。

## 固定流程图

V1 使用固定流程，不做自由动态调度。

```text
Root Delivery Task
  -> Intake / Issue Draft
  -> Create GitHub Issue
  -> Bootstrap Worktree
  -> Architect Plan
  -> Design Review
  -> Implementation
  -> Spec Review
  -> QA
  -> Create PR
  -> CI Watch
  -> PR Comments Loop
  -> Closeout
```

失败回路：

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
  -> Implementation Repair
  -> Spec Review
  -> QA
  -> CI Watch
  -> PR Comments Loop
```

## Human Gates

V1 必须保留以下人工确认点。

### Gate A：Issue Draft Approval

在创建 GitHub issue 前阻塞。

用户确认：

- issue title
- module
- task type
- scope in / out of scope
- acceptance commands
- definition of done
- suggested Project 8 status

### Gate B：Plan Approval

Architect 输出 plan 后阻塞。

用户确认：

- 是否进入开发
- 是否需要拆 phase
- 是否存在 scope 误解

### Gate C：High Risk Operation

以下情况必须阻塞：

- DB reset
- migration
- destructive command
- 修改 `odoo/` 或 `addons/`
- 大范围 refactor
- 修改生产数据或外部服务状态

### Gate D：PR Comment Automation

PR 打开后，处理 review comments 前默认先让 PR Comment Resolver 分类并给出
建议；是否自动修复需要用户确认。

### Gate E：Final Closeout

最终不自动 merge。Closeout 只汇报状态和建议下一步。

## 角色与职责

### Orchestrator

Profile：`odoo-hedge-orchestrator`

职责：

- 接收 root delivery task。
- 创建 child task graph。
- 串联 dependencies。
- 写入每个阶段的输入、输出和 gate 条件。
- 监控 blockers。
- 汇总最终结果。

禁止：

- 不写业务代码。
- 不创建实现 commit。
- 不绕过 Reviewer / QA。

### Intake / Issue Agent

V1 可以由 Orchestrator 创建 task，assignee 暂定 `odoo-hedge-architect`。

职责：

- 把用户自然语言需求转成 issue draft。
- 调用现有 `.codex` 经验：`gh-project-task-status`。
- 在创建 GitHub issue 前进入 Gate A。

### Worktree Agent

V1 assignee 暂定 `odoo-hedge-coder`。

职责：

- 使用 `scripts/codex-worktree.sh create <branch> master --no-codex`。
- 记录 branch、worktree path、main DB、test DB、ports。
- 不在 `master` 上实现。

参考现有 `.codex` skill：`issue-worktree-bootstrap`。

### Architect

Profile：`odoo-hedge-architect`

职责：

- 读取 `/home/user/Repos/odoo-hedge/AGENTS.md`。
- 读取 `/home/user/Repos/odoo-hedge/hedge_docs/README.md`。
- 读取 issue / spec / relevant docs。
- 输出 implementation-ready plan。
- 判断是否要拆 phase。

### Design Reviewer

Profile：`odoo-hedge-design-reviewer`

职责：

- 审核 plan 是否合理。
- 输出 `APPROVED` 或 `REQUEST_CHANGES`。
- 不写代码。

### Coder

Profile：`odoo-hedge-coder`

职责：

- 在 worktree 内实现。
- 保持 small diff。
- 运行最小有效验证。
- 输出 changed files、commands、results。

### Spec Reviewer

Profile：`odoo-hedge-spec-reviewer`

职责：

- 对照 issue / plan / acceptance criteria 验收实现。
- 输出 `spec_compliance=true/false`。
- 不主动修代码。

### QA

Profile：`odoo-hedge-qa`

职责：

- 运行 `python scripts/ci_check.py`。
- 运行最小相关 Odoo tests。
- 检查 naming、translation、XML、manifest、security。
- 输出 `qa_passed=true/false`。

### PR / CI / Comments / Closeout Agents

V1 可以先复用 `odoo-hedge-coder` 或 `odoo-hedge-qa` 执行，后续再拆独立 profiles：

- `odoo-hedge-pr-agent`
- `odoo-hedge-ci-watcher`
- `odoo-hedge-pr-comment-resolver`
- `odoo-hedge-closeout`

参考现有 `.codex` skills：

- `hedge-issue-delivery-loop`
- `hedge-ci-watch-repair-loop`
- `gh-pr-review-followup`
- `issue-closeout-sync`

## Kanban Task Graph Contract

Orchestrator 创建 child tasks 时，每个 task body 必须包含：

- parent/root task id
- assignee profile
- phase name
- required inputs
- expected outputs
- forbidden actions
- unblock condition
- next task id 或创建规则

每个 task 完成时必须输出：

- `Latest summary`
- 关键 artifact URLs / paths
- verification result
- blocker 或 next action

## Worktree Contract

真实开发任务必须先创建 worktree。

默认规则：

- base ref：`master`
- branch 格式：`codex/issue-<num>-<short-topic>`
- worktree 创建命令：

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

## GitHub Issue Contract

每个真实 delivery 必须有 GitHub issue。

issue body 至少包含：

- Task Type
- Module
- Priority
- Scope In
- Out of Scope
- Acceptance Commands
- Definition of Done
- Risk and Rollback Plan
- References
- Exploratory Verification Hints（UI/user-facing task 才需要）

创建 issue 前必须经过 Gate A。

## PR Contract

PR 创建前必须满足：

- branch 已 push。
- local verification 已记录。
- `python scripts/ci_check.py` 已运行，除非任务是 docs-only 且明确说明。
- PR body 包含 issue link、scope、changed files、verification commands。

PR 创建后必须进入 CI Watch。

## CI Contract

CI Watcher 必须：

- 找到最新 PR run。
- 最多轮询 10 分钟。
- 成功则进入 PR Comments Loop。
- 失败则分类并创建 repair task。

失败分类优先使用：

- `ruff / format`
- `business enum governance`
- `error contract governance`
- `xml / view parse`
- `odoo install / upgrade`
- `test regression`
- `i18n untranslated`
- `other infra / runner`

## PR Comments Contract

PR Comment Resolver 必须：

1. 拉取 review comments / requested changes。
2. 分类：
   - `adopt`
   - `decline`
   - `needs clarification`
3. 在修改代码前进入 Gate D。
4. 只处理用户确认的 actionable comments。
5. 修复后重新走 Spec Review、QA、CI。
6. 留 PR follow-up comment。

## Closeout Contract

Closeout Reporter 必须汇总：

- root task id
- issue URL
- PR URL
- branch
- worktree path
- commits
- local tests
- CI status
- review comments status
- remaining risks
- user next action

不自动 merge。

## V1 验收标准

V1 成功标准：

- 用户提出自然语言需求后，Orchestrator 能创建完整 delivery graph。
- issue 创建前会阻塞等待用户确认。
- worktree 在非 master branch 创建。
- Coder 只在 worktree 内修改。
- Reviewer / QA 能在失败时把流程送回 repair loop。
- PR 创建后能进入 CI watch。
- PR comments 能分类并在确认后自动修复。
- 最终有 closeout report。

## 下一步实施

1. 更新 `odoo-hedge-orchestrator` skill，固化本 workflow。
2. 补齐 Architect / Reviewer / QA / PR / CI / Closeout role skills。
3. 用一个低风险真实需求验证 issue -> PR。
4. 接 Slack notification。
