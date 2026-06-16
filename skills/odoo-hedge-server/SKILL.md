---
name: odoo-hedge-server
description: "Operate Odoo sandbox lifecycle from Slack."
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [odoo, slack, sandbox, operations]
    related_skills: []
---

# Odoo Hedge Server Slack Operations

## Purpose

You are operating Docker-backed Odoo sandboxes from a Slack thread opened by `/odoo-hedge-server`.
Users do not need to repeat `/odoo-hedge-server` inside the thread.

Use these tools:
- `mcp_odoo_hedge_server_sandbox_healthz`
- `mcp_odoo_hedge_server_ensure_sandbox_version`
- `mcp_odoo_hedge_server_create_sandbox`
- `mcp_odoo_hedge_server_upgrade_sandbox`
- `mcp_odoo_hedge_server_list_sandboxes`
- `mcp_odoo_hedge_server_get_sandbox`
- `mcp_odoo_hedge_server_destroy_sandbox`
- `mcp_odoo_hedge_server_provision_sync_defaults`

## Forward-Compatible Tool Map

Treat the listed tools above as the stable core map. Do not change the known
create, upgrade, status, and destroy workflows just because other MCP tools
exist.

When a user asks for an Odoo sandbox capability that is not covered by the
stable core workflows, discover the current MCP operation map before answering
or improvising:

1. Call `mcp_odoo_hedge_server_list_prompts`; if a prompt name or description
   mentions tool map, operations, capabilities, workflow, or sandbox actions,
   call `mcp_odoo_hedge_server_get_prompt` for that prompt.
2. Call `mcp_odoo_hedge_server_list_resources`; if a resource URI, name, or
   description mentions tool map, operations, capabilities, workflow, or
   sandbox actions, call `mcp_odoo_hedge_server_read_resource` for that
   resource.
3. Use the returned map to choose the relevant `mcp_odoo_hedge_server_*` tool
   and its required arguments.

If the operation map exposes a new destructive or state-changing tool, ask for
explicit confirmation before calling it. If the map is unavailable, ambiguous,
or conflicts with the user request, ask one short clarifying question instead
of guessing. Never use map discovery to bypass the confirmation and target
rules in this skill.

## Intent Routing

- Create: create, deploy, start, new, 创建, 部署, 启动, 新建.
- Upgrade: upgrade, update, switch version, replace version, 升级, 更新, 切换版本.
- List/status: list, status, get, show, 状态, 列状态, 查看, 查询.
- Destroy: destroy, delete, remove, stop, 销毁, 删除, 关闭, 停止.

If the user's intent is unclear, ask one short question in the same Slack thread.

## Create Requirements

For create, collect:
- `owner`: use the Slack requester from the thread context. If unavailable, ask for the username.
- deployment target: one of `branch`, `commit`, `tag`, or explicit default.
- optional `slug`: only pass it if the user explicitly names it.

Target rules:
- A 7-40 character hex value is `commit`.
- A value with exactly three numeric parts separated by dots, such as `2026.6.1`, is `tag`.
- A normal version or branch such as `17.0`, `18.0`, `master`, or `feature/x` is `branch`.
- If the user says default, 默认, 默认值, or 缺省, do not pass `branch`, `commit`, or `tag`.

Slug rules:
- Treat phrases like `slug demo-a`, `slug: demo-a`, `服务名 demo-a`, `名字叫 demo-a`, or `sandbox demo-a` as a custom `slug`.
- If no slug is provided, omit `slug` so the server generates one.

Before creating, if the target is missing and the user did not explicitly request defaults, ask for a version, commit, tag, or default.

Before calling `mcp_odoo_hedge_server_create_sandbox` with a `branch`, `commit`, or `tag`, first call `mcp_odoo_hedge_server_ensure_sandbox_version` with exactly the same target fields. If that check fails, do not create the sandbox. If the user explicitly requests default, do not call `mcp_odoo_hedge_server_ensure_sandbox_version`.

## Create Workflow

1. For a non-default target, call `mcp_odoo_hedge_server_ensure_sandbox_version` with exactly the target fields the user provided.
2. Call `mcp_odoo_hedge_server_create_sandbox` with `owner`, optional `slug`, and exactly the target fields the user provided.
3. Do not call `mcp_odoo_hedge_server_provision_sync_defaults` in the same tool batch as `mcp_odoo_hedge_server_create_sandbox`.
4. If create succeeds, send a visible interim Slack message saying:
   `Odoo sandbox 已创建，正在写入信易账户。`
   Include the returned `url` as 访问域名 and `db_name` as 数据库名字 when present.
   This interim message must stay in the current Slack thread.
5. Then call `mcp_odoo_hedge_server_provision_sync_defaults` with the returned `slug`.
6. Final response must include:
   - 访问域名
   - 数据库名字
   - whether 信易账户写入 succeeded

If create fails, do not call provisioning. If create succeeds but provisioning fails, say the sandbox was created and 信易账户写入 failed. Still include 访问域名 and 数据库名字 from the create result.

## Upgrade Workflow

For upgrade, collect:
- `slug`: require an explicit sandbox slug.
- deployment target: require exactly one of `branch`, `commit`, or `tag`.

Use the same target rules as create. Treat forms like `升级 demo-a 到 2026.6.2`, `upgrade demo-a to feature/x`, or `切换 demo-a commit abcdef123` as `slug=demo-a` plus the target. If slug or target is missing or ambiguous, ask for the missing piece. Do not treat default, 默认, 默认值, or 缺省 as a sufficient upgrade target.

Before calling `mcp_odoo_hedge_server_upgrade_sandbox`, call `mcp_odoo_hedge_server_ensure_sandbox_version` with exactly the same target fields. If that check fails, do not upgrade the sandbox.

Call `mcp_odoo_hedge_server_upgrade_sandbox` with `slug` and exactly the target fields the user provided. The upgrade keeps the sandbox database, filestore, slug, and routing resources. Do not call `mcp_odoo_hedge_server_provision_sync_defaults` after upgrade unless the user explicitly asks for 信易账户写入.

Final response must include the slug, target, upgrade result, and returned `url` or `db_name` when present. If upgrade fails, surface the backend `code`, `message`, and useful `details`.

## Status And Destroy

For status:
- If a slug is present, call `mcp_odoo_hedge_server_get_sandbox`.
- Otherwise call `mcp_odoo_hedge_server_list_sandboxes`.

For destroy:
- Require an explicit sandbox slug.
- Require clear confirmation before calling `mcp_odoo_hedge_server_destroy_sandbox`, such as `确认销毁 <slug>` or `yes, destroy <slug>`.
- If either slug or confirmation is missing, ask for the missing piece.

## Replies

Keep Slack replies concise, operational, and in the current thread. For backend failures, surface the `code`, `message`, and useful `details` fields. If the error details mention Docker image build failure, explain that the backend failed while building or ensuring the sandbox image.
