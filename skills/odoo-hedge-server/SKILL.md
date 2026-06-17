---
name: odoo-hedge-server
description: Operate Odoo sandbox lifecycle from Slack.
version: 1.1.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [odoo, slack, sandbox, operations]
    related_skills: []
---

# Odoo Hedge Server MCP

Use the `odoo-hedge-server` MCP tools to operate disposable Odoo hedge
sandboxes. Treat this skill as the routing and safety policy for sandbox
lifecycle work, Odoo module installation, and Odoo shell inspection.

In Slack, users operate from a thread opened by `/odoo-hedge-server`. They do
not need to repeat `/odoo-hedge-server` inside the thread.

## Prerequisites

- The `odoo-hedge-server` MCP server must be reachable.
- Prefer the server resources when available:
  - `hedge-sandbox://guide/overview`
  - `hedge-sandbox://guide/workflows`
  - `hedge-sandbox://guide/safety`
  - `hedge-sandbox://guide/errors`
  - `hedge-sandbox://operations/map`
  - `hedge-sandbox://guide/operation-map-format`
  - `hedge-sandbox://state/sandboxes`

Never bypass the MCP server with terminal commands, Docker commands, curl, or
direct HTTP calls for sandbox operations.

## Tool Naming

The MCP server publishes raw tool names such as `install_module` and
`get_operation`. Hermes usually exposes them with the server prefix:
`mcp_odoo_hedge_server_install_module`,
`mcp_odoo_hedge_server_get_operation`, and so on.

When this skill names a raw MCP tool, call the corresponding Hermes tool if the
prefixed name is what tool discovery exposes. If a required tool is missing,
ask the operator to reload MCP tools before trying the workflow.

## Runtime Discovery

Prefer live MCP discovery over hardcoded assumptions. At the start of an
operation, or when the requested capability is not clear:

1. List server resources.
2. Read `hedge-sandbox://operations/map` when present.
3. Read `hedge-sandbox://guide/workflows`,
   `hedge-sandbox://guide/safety`, or
   `hedge-sandbox://guide/operation-map-format` only when the operation map is
   unclear for the current task.
4. Use prompts only when resources do not clearly select an operation.

Use the operation map as the routing layer: match user intent to an operation
id, validate required inputs, run preconditions, then run steps in order.

## Durable Operations

Long-running MCP tools return durable operation records. When any tool returns
an `operation_id`, capture it immediately, report it to the user when useful,
and use the operation tools instead of re-submitting the same action.

- Use `get_operation` to inspect a known operation.
- Use `list_operations` to recover work after an interruption or missing id.
- Use `wait_operation` for bounded polling only. The server caps each wait at
  30 seconds, so a timeout or `running` status is not a final failure.
- Use `cancel_operation` only when the user asks to cancel or when continuing
  would be unsafe.
- Read `status`, `result_json`, `error_json`, and `links` before deciding the
  next action.
- Do not start a duplicate create, upgrade, install, provisioning, or shell
  task while an existing operation for the same intent is still queued or
  running.

If a Hermes-side timeout, reconnect, or chat interruption occurs, recover by
calling `get_operation` with the saved id or `list_operations` for recent work.
Do not assume the backend action stopped.

## Intent Routing

Use these intent ids from `hedge-sandbox://operations/map` when translating
user requests into tool calls:

- `sandbox.healthz`: check service health with `sandbox_healthz`.
- `sandbox.list`: list sandboxes with `list_sandboxes`.
- `sandbox.get`: inspect one sandbox with `get_sandbox`.
- `sandbox.create`: prepare source, create a sandbox, then provision defaults.
- `sandbox.upgrade`: prepare source, upgrade a sandbox, then inspect it.
- `sandbox.destroy`: destroy a sandbox.
- `odoo.module.install`: install one Odoo module.
- `odoo.inspect.with_shell`: run readonly Odoo shell inspection.
- `odoo.mutate.with_shell`: run mutating Odoo shell code after confirmation.
- `odoo.sync.provision_defaults`: provision sync defaults in an existing
  sandbox.

If the user's intent is unclear, ask one short question in the same Slack
thread. If the live operation map exposes a better matching operation than the
examples above, use the live map.

## Safety Policy

Classify each request before calling tools:

- `readonly`: health, list, get, and readonly shell inspection.
- `mutating_control`: create, upgrade, install module, and provision defaults.
- `mutating_shell`: Odoo shell code that writes data.
- `destructive_control`: destroy sandbox.

For `mutating_control`, state the target slug and requested action before
calling the tool when the user request is not already explicit.

For `mutating_shell`, never guess confirmation. If the tool asks for
confirmation, repeat the exact expected string from the tool error. The current
format is:

```text
确认在 {slug} 执行写操作
```

For `destructive_control`, ask for explicit confirmation naming the slug before
calling the tool.

## Source Version Rules

Sandboxes can target a `commit`, `branch`, or `tag`. If the user asks for a
specific source version, pass exactly one of those fields.

If the user asks for latest master, use `ensure_sandbox_version` first. If the
server rejects `branch="master"` as already default, retry without a source
target so the server can resolve the default source. `ensure_sandbox_version`
returns an operation envelope; wait for it and read `result_json` before using
the resolved source in later steps.

If the target is a commit, require a full commit SHA unless the user explicitly
confirms a shorter ref is accepted by the server.

Target parsing:

- A 7-40 character hex value is a `commit`, but prefer a full SHA.
- A value with exactly three numeric parts separated by dots, such as
  `2026.6.1`, is a `tag`.
- A normal version or branch such as `17.0`, `18.0`, `master`, or
  `feature/x` is a `branch`.
- If the user says default, 默认, 默认值, or 缺省, do not pass `branch`,
  `commit`, or `tag`.

Slug parsing:

- Treat phrases like `slug demo-a`, `slug: demo-a`, `服务名 demo-a`,
  `名字叫 demo-a`, or `sandbox demo-a` as a custom `slug`.
- If no slug is provided for create, omit `slug` so the server generates one.

## Create Workflow

For `sandbox.create`:

1. Collect `owner`. Use the Slack requester from thread context when
   available; otherwise ask for the username.
2. Collect optional `slug` only when the user explicitly names it.
3. Collect one source target: `branch`, `commit`, `tag`, or explicit default.
   If the target is missing and the user did not explicitly request defaults,
   ask for a version, commit, tag, or default.
4. If the request includes a non-default source target or asks for latest
   master, call `ensure_sandbox_version`.
5. Capture its `operation_id`, call `wait_operation`, then call
   `get_operation` and read `result_json`.
6. Call `create_sandbox` with the requested `owner`, optional `slug`, and
   resolved source target.
7. Capture the create `operation_id`. If completion may take longer than the
   current turn, tell the user the operation id and current status.
8. Poll with `wait_operation` and `get_operation` until the create operation is
   terminal or until it is better to return a progress update.
9. Call `get_sandbox` to report URL, slug, commit, branch/tag if present, and
   state.
10. If create succeeded and defaults are needed, send this visible interim
   Slack-thread message:

```text
Odoo sandbox 已创建，正在写入信易账户。
```

11. Call `provision_sync_defaults`, capture its `operation_id`, and wait/poll
    the same way.

Do not collapse create and default provisioning into an assumed single backend
step. They are separate operations.

If create fails, do not call provisioning. If create succeeds but provisioning
fails, say the sandbox was created and 信易账户写入 failed. Final create replies
must include 访问域名, 数据库名字 when present, and whether 信易账户写入 succeeded.

## Upgrade Workflow

For `sandbox.upgrade`:

1. Collect explicit `slug`.
2. Collect exactly one target: `branch`, `commit`, `tag`, or latest master.
   Do not treat default, 默认, 默认值, or 缺省 as a sufficient upgrade target.
3. Call `get_sandbox` and verify the slug exists.
4. Call `ensure_sandbox_version` and wait for its operation result.
5. Call `upgrade_sandbox` with the slug and resolved source target.
6. Capture the upgrade `operation_id`.
7. Poll with `wait_operation` and `get_operation`.
8. After terminal success, call `get_sandbox` and report the resulting state,
   URL, database name when present, and source version.

If an upgrade operation is still running, return the operation id and status
instead of starting another upgrade.

Do not run `provision_sync_defaults` after upgrade unless the user explicitly
asks for 信易账户写入.

## Module Install Workflow

For `odoo.module.install`, call `install_module` with one slug and one module.
The tool now submits an operation and returns immediately.

1. Call `get_sandbox` and require `running` state before install.
2. Call `install_module(slug=..., module=...)`.
3. Capture the returned `operation_id`.
4. Poll with `wait_operation` and `get_operation`.
5. Inspect `error_json` for runtime install failures even if the MCP call
   itself succeeded.
6. On terminal success, summarize `result_json` and optionally verify with a
   readonly `run_odoo_shell` check.

If the user asks to install the ERP suite, install:

```text
hedge_erp_suite
```

For bulk module installs, process modules sequentially. Finish or fail one
operation before submitting the next. Stop on pool exhaustion, registry errors,
or module install failures unless the user explicitly asks to continue.

When practical after install success, run a readonly shell check for module
state so the final reply distinguishes submitted, installed, and verified.

## Odoo Shell Workflow

`run_odoo_shell` also returns an operation envelope.

- Use `mode="readonly"` for inspection and verification. Readonly mode rolls
  back after execution.
- Use `mode="mutating"` only with the exact confirmation string required by
  the tool.
- Always provide a concise `reason`.
- Capture `operation_id`, poll with `wait_operation`, then inspect
  `result_json` or `error_json`.

## Status, Recovery, and Cancellation

When a user asks for status:

1. If they provide an operation id, call `get_operation`.
2. If they provide only a slug or recent context, call `list_operations` and
   `get_sandbox`.
3. If an operation is running, optionally call `wait_operation` once for a
   short bounded wait, then report the latest status.

When a user asks to destroy a sandbox, require an explicit slug and clear
confirmation such as `确认销毁 <slug>` or `yes, destroy <slug>`.

When a user asks to cancel a running operation, call `get_operation` first. If
it is still cancelable, call `cancel_operation` and then `get_operation` again
to report the result.

## Response Style

Keep replies operational and concrete:

- Mention the slug and operation id for submitted work.
- Distinguish submitted, running, succeeded, failed, canceled, and unknown.
- Include sandbox URL only after `get_sandbox` or operation `result_json`
  confirms it.
- For failures, summarize `error_json` with the actionable part first.
- For long-running operations, say that the backend is still running and give
  the next status command or operation id.

## Direct Tool Names

When tool discovery exposes raw names instead of intent ids, map them as
follows:

- `sandbox_healthz`
- `ensure_sandbox_version`
- `create_sandbox`
- `upgrade_sandbox`
- `install_module`
- `run_odoo_shell`
- `list_sandboxes`
- `get_sandbox`
- `destroy_sandbox`
- `provision_sync_defaults`
- `get_operation`
- `list_operations`
- `wait_operation`
- `cancel_operation`
