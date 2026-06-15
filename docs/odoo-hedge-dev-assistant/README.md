# Odoo Hedge Dev Assistant Docs

This directory tracks the local project of using Hermes Agent as a development
assistant for `/home/user/Repos/odoo-hedge`.

It is not the upstream Hermes development guide. The upstream guide remains the
root `AGENTS.md`.

## Purpose

The goal is to build an incremental, maintainable setup where Hermes can support
`odoo-hedge` development through:

- CLI/TUI for day-to-day coding work
- Slack for async task intake, progress, and results
- web dashboard for sessions, logs, skills, gateway, and browser chat
- OpenAI Codex as the main coding provider
- DeepSeek as a validated auxiliary or delegated provider

## Source Boundaries

- Hermes upstream rules: `../../AGENTS.md`
- Odoo Hedge repo rules: `/home/user/Repos/odoo-hedge/AGENTS.md`
- Odoo Hedge authoritative docs: `/home/user/Repos/odoo-hedge/hedge_docs/README.md`
- Local Hermes config: `~/.hermes/config.yaml`
- Local Hermes secrets: `~/.hermes/.env`

## Documentation Sections

本目录下的正式文档正文默认使用中文。专业名词、命令、配置键、模型名、
产品名、API 名称、路径、文件名、代码符号、协议名，以及难以准确翻译的概念，
保留英文原文。

- `plans/` - 分阶段实施计划和可执行 checklist
- `decisions/` - 关于 profile、provider、Slack、web、plugin 等长期决策
- `runbooks/` - 设置、启动、排障、运维类操作手册
- `inventory/` - 已发现的 skills、scripts、configs、资产和缺口盘点

实际 `odoo-hedge` 开发任务仍以 GitHub Issue / Project 8 和
`/home/user/Repos/odoo-hedge/hedge_docs/` 为事实源；本目录只记录 Hermes
开发助手系统本身的搭建、使用和改进。

## Current Workflow Entry

当前 delivery workflow 入口：

- `plans/2026-06-04-delivery-workflow-v1.md`

用户提供已有 GitHub issue 后，默认创建一个 Kanban root delivery task，由
`odoo-hedge-orchestrator` 从 `Bootstrap Worktree` 开始编排 worktree、plan、
implementation、review、QA、PR、CI、PR comments 和 closeout。

如果没有 issue，流程先阻塞，等待用户确认是否创建 issue；创建 issue 不属于
默认 delivery graph。

查看过程：

- Dashboard：`/kanban`
- CLI：`hermes -p odoo-hedge-dev kanban --board odoo-hedge-dev list`
- 单任务详情：`hermes -p odoo-hedge-dev kanban --board odoo-hedge-dev show <task_id>`

## First Milestones

1. Make the `hermes` command reliable on this development machine.
2. Create a dedicated `odoo-hedge` Hermes profile.
3. Wire `terminal.cwd` to `/home/user/Repos/odoo-hedge`.
4. Add external skill directories for existing local and repo-specific skills.
5. Create development-focused Hermes skills for `odoo-hedge`.
6. Enable Slack gateway with narrow user/channel allowlists.
7. Enable dashboard chat with `hermes dashboard --tui`.
8. Decide when API Server integration is actually needed.
