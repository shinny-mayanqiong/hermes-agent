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

## Prerequisites

The `odoo-hedge-server` MCP server must be registered for this session. Hermes
exposes server tool `example_tool` as `mcp_odoo_hedge_server_example_tool`.
If no `mcp_odoo_hedge_server_*` tools are available, ask the operator to reload
or restart the gateway; do not bypass the MCP server with terminal, curl, Docker,
or direct HTTP calls.

## Runtime Operation Map

Prefer live MCP discovery over hardcoded tool lists. At the start of each
requested operation, or whenever the requested capability is not already clear:

1. Call `mcp_odoo_hedge_server_list_resources`.
2. Read `hedge-sandbox://operations/map` with
   `mcp_odoo_hedge_server_read_resource` when it is present. If that exact URI
   is absent, read the resource whose URI, name, or description best matches
   operation map, operations, capabilities, workflow, or sandbox actions.
3. Read `hedge-sandbox://guide/operation-map-format`,
   `hedge-sandbox://guide/workflows`, or `hedge-sandbox://guide/safety` only
   when the map fields are unclear for the current task.
4. Call `mcp_odoo_hedge_server_list_prompts` and then
   `mcp_odoo_hedge_server_get_prompt` only when the map does not clearly choose
   an operation, or when a prompt describes the requested workflow more directly.

Use the operation map as the routing layer: match the user intent to an
operation id, validate the operation's required inputs, run its preconditions,
then run its steps in order. Translate every step tool name from the map to the
Hermes tool name by prefixing `mcp_odoo_hedge_server_`. Do not add new server
tool names to this skill just because the MCP server grows; new capabilities
should come from the operation map, resources, prompts, and current tool schemas.

If a mapped tool is not registered in the session, say the MCP tools need to be
reloaded before the request can be executed. Do not guess a terminal workaround.

## Intent Routing

- Create: create, deploy, start, new, 创建, 部署, 启动, 新建. Prefer operation `sandbox.create`.
- Upgrade: upgrade, update, switch version, replace version, 升级, 更新, 切换版本. Prefer operation `sandbox.upgrade`.
- List/status: list, status, get, show, 状态, 列状态, 查看, 查询. Prefer `sandbox.list` or `sandbox.get`.
- Destroy: destroy, delete, remove, stop, 销毁, 删除, 关闭, 停止. Prefer `sandbox.destroy`.
- In-sandbox Odoo work: module install, Odoo shell inspection, data repair, sync defaults, or similar. Prefer the matching `odoo.*` operation from the live map.

If the user's intent is unclear, ask one short question in the same Slack thread.

The operation ids above are examples from the current server, not an exhaustive
contract. If the live map exposes a better matching operation, use it.

## Safety

Follow the operation map's `risk` and `confirmation` fields:

- `readonly`: execute when intent and required inputs are clear.
- `mutating_control`: execute when the user explicitly requested the state change and the target is clear; otherwise ask for confirmation.
- `mutating_shell`: require explicit confirmation of the intent and sandbox. If the tool error returns an exact expected confirmation string, retry only with that exact value after the user confirms it.
- `destructive_control`: require explicit confirmation such as `确认销毁 <slug>` or `yes, destroy <slug>`.

Never use map discovery to bypass target rules, confirmation rules, or the MCP
server boundary.

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

Before creating, if the target is missing and the user did not explicitly
request defaults, ask for a version, commit, tag, or default. For non-default
targets, run the map's version-ensure step before the create step. If the ensure
step fails, do not create the sandbox.

## Create Workflow

1. Execute operation `sandbox.create` from the live operation map, passing
   `owner`, optional `slug`, and exactly the target fields the user provided.
2. Do not batch create with any post-create provisioning step.
3. If create succeeds, send a visible interim Slack message saying:
   `Odoo sandbox 已创建，正在写入信易账户。`
   Include the returned `url` as 访问域名 and `db_name` as 数据库名字 when present.
   This interim message must stay in the current Slack thread.
4. Then execute operation `odoo.sync.provision_defaults` with the returned
   `slug` when the live map exposes it. If the map is unavailable but legacy
   direct tools are registered, use `mcp_odoo_hedge_server_provision_sync_defaults`.
5. Final response must include:
   - 访问域名
   - 数据库名字
   - whether 信易账户写入 succeeded

If create fails, do not call provisioning. If create succeeds but provisioning fails, say the sandbox was created and 信易账户写入 failed. Still include 访问域名 and 数据库名字 from the create result.

## Upgrade Workflow

For upgrade, collect:
- `slug`: require an explicit sandbox slug.
- deployment target: require exactly one of `branch`, `commit`, or `tag`.

Use the same target rules as create. Treat forms like `升级 demo-a 到 2026.6.2`, `upgrade demo-a to feature/x`, or `切换 demo-a commit abcdef123` as `slug=demo-a` plus the target. If slug or target is missing or ambiguous, ask for the missing piece. Do not treat default, 默认, 默认值, or 缺省 as a sufficient upgrade target.

Execute operation `sandbox.upgrade` from the live operation map with `slug` and
exactly the target fields the user provided. The map should ensure the target
version before upgrading; if the ensure step fails, do not upgrade the sandbox.
The upgrade keeps the sandbox database, filestore, slug, and routing resources.
Do not run `odoo.sync.provision_defaults` after upgrade unless the user
explicitly asks for 信易账户写入.

Final response must include the slug, target, upgrade result, and returned `url` or `db_name` when present. If upgrade fails, surface the backend `code`, `message`, and useful `details`.

## Status And Destroy

For status:
- If a slug is present, execute `sandbox.get`.
- Otherwise execute `sandbox.list`.

For destroy:
- Require an explicit sandbox slug.
- Require clear confirmation before executing `sandbox.destroy`, such as `确认销毁 <slug>` or `yes, destroy <slug>`.
- If either slug or confirmation is missing, ask for the missing piece.

## Legacy Fallback

Use these direct tools only when MCP resources/prompts are unavailable or the
server is older than the operation-map contract:

- `mcp_odoo_hedge_server_sandbox_healthz`
- `mcp_odoo_hedge_server_ensure_sandbox_version`
- `mcp_odoo_hedge_server_create_sandbox`
- `mcp_odoo_hedge_server_upgrade_sandbox`
- `mcp_odoo_hedge_server_list_sandboxes`
- `mcp_odoo_hedge_server_get_sandbox`
- `mcp_odoo_hedge_server_destroy_sandbox`
- `mcp_odoo_hedge_server_provision_sync_defaults`

## Replies

Keep Slack replies concise, operational, and in the current thread. For backend failures, surface the `code`, `message`, and useful `details` fields. If the error details mention Docker image build failure, explain that the backend failed while building or ensuring the sandbox image.
