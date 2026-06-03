# 2026-06-03 多角色 Agent 编排实施计划

## 目标

为 `odoo-hedge-dev` 建立一套多角色开发 agent 流程，在 Codex 外围增加
Hermes 编排层。

目标效果：

- 用户提交一个 issue 或开发任务。
- Orchestrator 判断任务大小并创建流程。
- Architect、Reviewer、Coder、QA 等角色按阶段执行。
- 用户可以通过 Kanban dashboard / CLI / 后续 Slack 通知看到执行进度。
- 每个阶段有明确输入、输出、通过标准和阻塞条件。

## 当前状态

已完成：

- `odoo-hedge-dev` profile 已创建。
- `terminal.cwd` 已由用户确认完成配置。
- `openai-codex` auth 已重新登录成功。
- Codex 是开发主力。
- `odoo-hedge-dev` Kanban board 已由用户确认创建。
- 6 个角色 profiles 已创建。
- 首批 2 个 role skills 已创建：
  `odoo-hedge-orchestrator`、`odoo-hedge-coder`。

待完成：

- 定义固定流程模板。
- 接入通知。
- 为 Architect、Design Reviewer、Spec Reviewer、QA 补充 role skills。
- 运行一次低风险 dry run。

## 阶段 1：定义角色 profiles

已创建这些 profiles：

```text
odoo-hedge-orchestrator
odoo-hedge-architect
odoo-hedge-design-reviewer
odoo-hedge-coder
odoo-hedge-spec-reviewer
odoo-hedge-qa
```

### Profile 描述

每个 profile 必须有 description，供 Kanban decomposer / orchestrator 路由：

```bash
hermes profile describe odoo-hedge-orchestrator --text "<description>"
```

描述应说明：

- 角色职责
- 不做什么
- 适合接收什么任务
- 是否允许写代码

## 阶段 2：定义各角色 skill

目标 skills：

```text
odoo-hedge-orchestrator
odoo-hedge-architect
odoo-hedge-design-reviewer
odoo-hedge-coder
odoo-hedge-spec-reviewer
odoo-hedge-qa
```

这些是开发人员 workflow skills，不复用业务用户
`~/.hermes/skills/domain/odoo-hedge*`。

已创建：

- `~/.hermes/profiles/odoo-hedge-orchestrator/skills/dev/odoo-hedge-orchestrator/SKILL.md`
- `~/.hermes/profiles/odoo-hedge-coder/skills/dev/odoo-hedge-coder/SKILL.md`

待创建：

- `odoo-hedge-architect`
- `odoo-hedge-design-reviewer`
- `odoo-hedge-spec-reviewer`
- `odoo-hedge-qa`

### Orchestrator skill

要求：

- 只做编排，不直接实现。
- 先读取 profile roster。
- 接收用户任务后，创建 Architect task。
- 根据 Architect 输出创建后续 task。
- 用 `kanban_create` / `kanban_link` / `kanban_comment` 编排。
- 在关键节点通知用户。

### Architect skill

要求：

- 先读 `odoo-hedge/AGENTS.md` 和 `hedge_docs/README.md`。
- 整理 issue 状态和关联 docs。
- 判断任务大小。
- 小任务输出 implementation-ready plan。
- 大任务拆 phases。
- 输出 acceptance criteria、verification、risk、rollback。

### Design Reviewer skill

要求：

- 对 Architect 输出做 review。
- 检查 scope、边界、遗漏风险。
- 输出 `APPROVED` 或 `REQUEST_CHANGES`。
- 不写代码。

### Coder skill

要求：

- 按 spec/phase 执行。
- 使用 Codex 作为主力。
- 保持小 diff。
- 按 `odoo-hedge` 规则运行验证。
- 输出 changed files、tests run、remaining gaps。

### Spec Reviewer skill

要求：

- 对照 spec / issue acceptance 检查实现是否完成。
- 输出遗漏点和是否可进入 QA。
- 不做大范围修改。

### QA skill

要求：

- 运行 `python scripts/ci_check.py`。
- 运行最小相关 Odoo 测试。
- 检查命名、翻译、XML、manifest、security CSV。
- 对 UI 任务触发 Playwright debug/audit 流程。
- 输出 pass/fail、失败证据和建议 repair task。

## 阶段 3：建立 Kanban board

建议 board：

```text
odoo-hedge-dev
```

命令：

```bash
cd /home/user/Repos/hermes-agent
source .venv/bin/activate

hermes kanban boards create odoo-hedge-dev \
  --name "Odoo Hedge Dev" \
  --description "Multi-role development workflow for odoo-hedge" \
  --switch
```

启动 dashboard：

```bash
hermes dashboard --tui
```

启动 gateway / dispatcher：

```bash
hermes gateway start
```

## 阶段 4：固定流程模板

### 小任务模板

```text
Architect -> Design Reviewer -> Coder -> Spec Reviewer -> QA -> Orchestrator closeout
```

### 大任务模板

```text
Architect -> Design Reviewer -> Phase split
  -> Phase N Coder
  -> Phase N Spec Reviewer
  -> Phase N QA
-> Orchestrator integration closeout
```

### 失败回路

Design Reviewer request changes：

```text
Design Reviewer -> Architect repair -> Design Reviewer
```

Spec Reviewer request changes：

```text
Spec Reviewer -> Coder repair -> Spec Reviewer
```

QA failed：

```text
QA -> Coder repair -> Spec Reviewer -> QA
```

## 阶段 5：通知机制

第一阶段：

- 使用 Kanban comments。
- 使用 `hermes kanban watch`。
- 使用 dashboard Kanban 页面。

第二阶段：

- 接 Slack gateway。
- 用 Kanban notification subscription 或 gateway message 通知用户。

关键通知点：

- 任务已接收
- Architect 完成
- Design approved / request changes
- Coder blocked / complete
- Spec review approved / request changes
- QA pass/fail
- closeout complete

## 阶段 6：Dry Run

选择一个低风险任务：

- 文档类 issue
- 不改数据库
- 不涉及 UI 大流程
- 不需要 DB reset

Dry run 目标：

- 验证 board 可见。
- 验证 profiles 能被 dispatcher 启动。
- 验证依赖链能推进。
- 验证 reviewer 能 request changes。
- 验证用户能看到阶段状态。

## 验收标准

- 能创建 `odoo-hedge-dev` board。
- 能创建并列出 6 个角色 profile。
- 每个 profile 有 description。
- Orchestrator 能创建 child tasks 并链接依赖。
- Coder task 能使用 Codex 执行。
- Reviewer/QA 输出结构化结果。
- 用户能通过 dashboard 或 CLI 看到流程进度。

## 后续增强

- Slack 通知。
- API Server / dashboard 自定义视图。
- 用 local plugin 封装高频命令。
- 将 `hedge_ci_check`、worktree、Project 8 状态查询工具化。
