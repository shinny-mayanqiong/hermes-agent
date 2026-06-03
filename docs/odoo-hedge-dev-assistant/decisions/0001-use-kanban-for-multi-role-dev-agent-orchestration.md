# 0001 使用 Kanban 编排多角色开发 agents

## 状态

accepted

## 日期

2026-06-03

## 背景

用户希望在 Codex 外围增加一层开发流程编排，让 `odoo-hedge` 开发不只是单个
coding agent 执行，而是由多个固定角色协作：

- Architect Agent：整理 issues 状态，接受任务后分析 issue 大小，必要时拆分阶段。
- Design Reviewer Agent：review 设计是否合理。
- Coder Agent：执行开发。
- Spec Reviewer Agent：review 开发是否完成了 spec。
- QA Agent：测试、变量命名、翻译等其他检查。

用户还希望：

- 能在一个流程中看到这些 agents 的执行编排。
- 流程可以是固定流程，也可以有一个主持人用智能方式编排。
- 中间执行到关键节点时通知用户。

## 决策

使用 Hermes Kanban 作为多角色开发 agent 的持久编排层。

采用“主持人 + 固定阶段模板”的混合方式：

- Orchestrator / Host Agent 作为主持人，负责读取任务、整理 issue 状态、判断规模、
  创建子任务、链接依赖、推动阶段转换，并在关键节点通知用户。
- 固定阶段模板用于普通开发任务，保证流程可预期、可复盘。
- 对复杂任务，Orchestrator 可以把 Architect 阶段拆分为多个 child tasks。
- Codex 仍是 Coder Agent 的主力开发引擎。

## 为什么不用单纯 `delegate_task`

`delegate_task` 适合短生命周期、父 agent 等待子 agent 返回结果的场景。

本需求需要：

- 多个命名角色
- 跨步骤状态保留
- human-in-the-loop
- 可视化执行状态
- 可重试
- 可暂停/阻塞/恢复
- 通知用户
- 任务和评论持久化

这些更符合 Kanban 的能力边界。

## 角色设计

### Orchestrator / Host Agent

职责：

- 接收用户任务。
- 读取 `odoo-hedge` issue / Project 8 / docs 状态。
- 判断任务大小。
- 小任务走简化流程。
- 大任务拆分阶段。
- 创建 Kanban child tasks。
- 链接依赖。
- 在关键节点向用户汇报。
- 不直接写业务代码。

建议 profile：

```text
odoo-hedge-orchestrator
```

### Architect Agent

职责：

- 整理 issue 背景、scope、现有代码和文档。
- 判断是否需要 spec。
- 产出阶段划分、验收方式、风险和回滚。
- 对过大任务拆成合理 phases。

建议 profile：

```text
odoo-hedge-architect
```

### Design Reviewer Agent

职责：

- review Architect 的设计。
- 检查 scope 是否过大、是否违反 `odoo-hedge` 约束。
- 检查是否遗漏测试、i18n、权限、数据迁移、UI 验收等风险。
- 不写实现代码。

建议 profile：

```text
odoo-hedge-design-reviewer
```

### Coder Agent

职责：

- 按 spec 或阶段任务执行开发。
- 优先使用 Codex。
- 只改允许范围内的文件。
- 运行最小有效验证。
- 在完成时输出 changed files、tests run、known gaps。

建议 profile：

```text
odoo-hedge-coder
```

### Spec Reviewer Agent

职责：

- review Coder 是否完成 spec。
- 对照 spec / issue acceptance criteria 检查遗漏。
- 判断是否需要退回 Coder。
- 不做大范围重构。

建议 profile：

```text
odoo-hedge-spec-reviewer
```

### QA Agent

职责：

- 运行测试和质量检查。
- 检查变量命名、翻译、PO 文件、XML、manifest、security CSV 等。
- 对 UI 任务检查 Playwright/debug artifacts。
- 输出 pass/fail 和修复建议。

建议 profile：

```text
odoo-hedge-qa
```

## 标准流程

普通任务：

```text
User
  -> Orchestrator
  -> Architect
  -> Design Reviewer
  -> Coder
  -> Spec Reviewer
  -> QA
  -> Orchestrator closeout / notify user
```

复杂任务：

```text
User
  -> Orchestrator
  -> Architect
      -> Phase 1 plan
      -> Phase 2 plan
      -> Phase 3 plan
  -> Design Reviewer
  -> per phase:
      -> Coder
      -> Spec Reviewer
      -> QA
  -> Orchestrator integration review / notify user
```

## 通知策略

第一阶段使用 Kanban task comments、dashboard、CLI watch。

后续接入 Slack gateway：

- 任务创建后通知。
- Architect 完成后通知。
- Design Reviewer 发现重大问题时通知。
- Coder 完成或 blocked 时通知。
- Spec Reviewer request changes 时通知。
- QA fail/pass 时通知。
- 最终 closeout 时通知。

通知应包含：

- task id
- 当前阶段
- assignee/profile
- summary
- blocker 或 next action

## 风险

- 多角色流程可能过重，小任务不应强行跑完整链路。
- Reviewer 和 QA 如果没有明确 checklist，容易变成泛泛评论。
- Orchestrator 如果有 terminal/file/code tool，可能忍不住直接实现。
- Profile descriptions 不清晰会导致 Kanban decomposer 路由不准。
- Coder 和 Reviewer 同时改同一文件会造成冲突。

## 约束

- Orchestrator 不直接写业务代码。
- Reviewer 不直接做大范围实现。
- QA 不默认修代码；先报告，必要时创建 repair task。
- Coder 使用 Codex 作为主力。
- 所有 `odoo-hedge` 任务仍以 GitHub Issue / Project 8 / `hedge_docs` 为事实源。
- Hermes 文档目录只记录开发助手系统本身。

## 后续动作

1. 创建 profile 设计文档。
2. 创建各角色 skills。
3. 创建 `odoo-hedge-dev-orchestration` runbook。
4. 在 Kanban 建立 `odoo-hedge` board。
5. 用一个小 issue 做 dry run。
