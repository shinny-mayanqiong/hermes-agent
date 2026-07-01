# Mail Watch Docs - Agent Entry

This directory is the project-local entrypoint for documenting the `mail_watch`
migration into Hermes.

It is intentionally narrower than the repository root `AGENTS.md`. The root
file remains the source of truth for upstream Hermes architecture, contribution
rules, code style, and plugin/tool footprint decisions.

## Scope

Use this entrypoint for work about:

- documenting the current `mail_watch` implementation from
  `/home/user/Repos/ops-scripts/mail_watch`
- planning how to migrate `mail_watch` into Hermes
- recording config, env var names, cron/systemd scheduling, SQLite state, and
  Slack delivery boundaries
- writing runbooks for operating the Hermes-integrated mail watcher

Do not use this entrypoint as the source of truth for:

- generic Hermes development rules
- unrelated `ops-scripts` utilities
- IMAP passwords, Slack webhook URLs, tokens, or other secret values
- business process details beyond what is needed to operate the monitor safely

## Reading Order

1. `README.md`
2. Future `inventory/` docs for current-state facts
3. Future `plans/` docs for migration phases
4. Future `runbooks/` docs for operation and troubleshooting

## Language

本目录下未来新增或更新的正式文档，正文默认使用中文。

专业名词、命令、配置键、环境变量名、路径、文件名、代码符号和协议名，保留
English 原文。

## Operating Principles

- Preserve the existing `mail_watch` business behavior before refactoring.
- Prefer a Hermes plugin plus CLI/cron integration over adding a core model
  tool.
- Keep non-secret behavior config in Hermes `config.yaml` or plugin config when
  the migration reaches that phase.
- Keep IMAP credentials and Slack webhook URLs in `.env` or an external secret
  store; documentation may name variables but must not record values.
- Treat the existing SQLite database and mailbox checkpoint as stateful
  production data.
- Make migration steps reversible until the old crontab path is explicitly
  retired.

