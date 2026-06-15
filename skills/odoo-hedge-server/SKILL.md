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
- `odoo_sandbox_create`
- `odoo_sandbox_provision_sync_defaults`
- `odoo_sandbox_list`
- `odoo_sandbox_get`
- `odoo_sandbox_destroy`

## Intent Routing

- Create: create, deploy, start, new, 创建, 部署, 启动, 新建.
- List/status: list, status, get, show, 状态, 列状态, 查看, 查询.
- Destroy: destroy, delete, remove, stop, 销毁, 删除, 关闭, 停止.
- Unsupported: if the user asks to upgrade, tell them this HTTP API currently does not expose an upgrade endpoint.

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

Before calling `odoo_sandbox_create`, if the target is missing and the user did not explicitly request defaults, ask for a version, commit, tag, or default.

## Create Workflow

1. Call `odoo_sandbox_create` with `owner`, optional `slug`, and exactly the target fields the user provided.
2. Do not call `odoo_sandbox_provision_sync_defaults` in the same tool batch as `odoo_sandbox_create`.
3. If create succeeds, send a visible interim Slack message saying:
   `Odoo sandbox 已创建，正在写入信易账户。`
   Include the returned `url` as 访问域名 and `db_name` as 数据库名字 when present.
   This interim message must stay in the current Slack thread.
4. Then call `odoo_sandbox_provision_sync_defaults` with the returned `slug`.
5. Final response must include:
   - 访问域名
   - 数据库名字
   - whether 信易账户写入 succeeded

If create fails, do not call provisioning. If create succeeds but provisioning fails, say the sandbox was created and 信易账户写入 failed. Still include 访问域名 and 数据库名字 from the create result.

## Status And Destroy

For status:
- If a slug is present, call `odoo_sandbox_get`.
- Otherwise call `odoo_sandbox_list`.

For destroy:
- Require an explicit sandbox slug.
- Require clear confirmation before calling `odoo_sandbox_destroy`, such as `确认销毁 <slug>` or `yes, destroy <slug>`.
- If either slug or confirmation is missing, ask for the missing piece.

## Replies

Keep Slack replies concise, operational, and in the current thread. For HTTP failures, surface the backend `code`, `message`, and useful `details` fields. If the error details mention Docker image build failure, explain that the backend failed while building or ensuring the sandbox image.
