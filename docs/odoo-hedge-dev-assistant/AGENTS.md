# Odoo Hedge Dev Assistant - Agent Entry

This directory is the project-local entrypoint for turning Hermes Agent into a
development assistant for `/home/user/Repos/odoo-hedge`.

It is intentionally separate from the repository root `AGENTS.md`, which
describes how to develop upstream Hermes Agent itself.

## Scope

Use this entrypoint for work about:

- configuring Hermes as a development assistant for `odoo-hedge`
- project-specific Hermes profiles, skills, plugins, hooks, gateway settings,
  dashboard usage, and model routing
- adapting existing `odoo-hedge` Codex/Cursor assets into Hermes workflows
- documenting operational setup for CLI, Slack, and web surfaces

Do not use this entrypoint as the source of truth for:

- general Hermes upstream architecture or contribution rules
- `odoo-hedge` business rules, source-code conventions, or test commands
- secrets, tokens, passwords, endpoint credentials, or machine-private state

For upstream Hermes development, read the root `AGENTS.md`.
For `odoo-hedge` development rules, read `/home/user/Repos/odoo-hedge/AGENTS.md`
and then `/home/user/Repos/odoo-hedge/hedge_docs/README.md`.

## Reading Order

1. `README.md`
2. `plans/` for staged implementation plans
3. `decisions/` for durable local decisions
4. `runbooks/` for setup and operations procedures
5. `inventory/` for discovered local assets and integration notes

## Language

本目录下未来新增或更新的正式文档，正文默认使用中文。

专业名词、命令、配置键、模型名、产品名、API 名称、路径、文件名、代码符号、
协议名，以及难以准确翻译的概念，保留英文原文。

## Operating Principles

- Keep Hermes upstream code and local `odoo-hedge` assistant adaptation clearly
  separated.
- Prefer configuration, external skills, and user/local plugins before changing
  Hermes core.
- Keep `odoo-hedge` project knowledge in `odoo-hedge` docs or dedicated Hermes
  skills; do not bury it in generic Hermes docs.
- Treat Slack, CLI, and web as separate surfaces sharing the same underlying
  Hermes profile and session store.
- Use OpenAI Codex as the primary coding model until a task-specific reason says
  otherwise.
- Use DeepSeek primarily for lower-cost auxiliary tasks, summarization, first-pass
  review, and delegated subagents after validation.
- Never document secrets. Reference variable names and config locations only.

## Initial Integration Targets

- CLI/TUI: primary development surface.
- Web dashboard: session, logs, skills, tools, gateway, and browser-based TUI.
- Slack gateway: asynchronous task submission and progress/results delivery.
- API server: optional OpenAI-compatible backend for internal tools or Odoo UI
  integration after local development workflows are stable.

## Safety Boundaries

- Do not modify `/home/user/Repos/odoo-hedge` from this repository unless the
  user explicitly asks for implementation work there.
- Do not commit generated personal configuration into Hermes upstream paths.
- Do not add `odoo-hedge`-specific hardcoding to Hermes core if the same result
  can be achieved with profiles, skills, hooks, or plugins.
- Keep local-machine setup docs factual and reproducible, but avoid embedding
  absolute secrets or private credentials.
