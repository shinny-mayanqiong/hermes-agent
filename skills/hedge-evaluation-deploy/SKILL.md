---
name: hedge-evaluation-deploy
description: Manage the fixed Hedge evaluation environment from Slack by collecting, validating, confirming, and submitting broker.json through the hedge-evaluation-control MCP server. Use when a user asks to deploy, install, update, check, verify, inspect, wait for, or cancel an evaluation-environment operation, including requests started with /hedge-evaluation-deploy.
---

# Hedge Evaluation Deploy

Manage the single fixed evaluation environment through the
`hedge-evaluation-control` MCP server. In Slack, keep the whole workflow in the
thread opened by `/hedge-evaluation-deploy`.

## Safety Contract

- Use only the `hedge-evaluation-control` MCP tools for evaluation-environment
  actions. Do not replace them with terminal commands, direct HTTP requests,
  SSH, or the `odoo-hedge-server` sandbox MCP.
- Treat every submitted `broker.json` as sensitive. Never paste it back, quote
  `ctp_auth_code`, write it to a local file, call a memory tool with it, or
  deliberately create another persistent copy. Use the inbound conversation
  value only to validate and submit it to the MCP.
- Do not log or display full tool arguments. Summaries must mask secrets.
- The environment is fixed. Do not ask the user to choose a host, database, or
  deployment target.
- Deployment changes the shared evaluation environment. Require a separate,
  explicit confirmation for the current validated payload before calling the
  deployment tool.
- In a Slack workflow thread, accept deployment or cancellation confirmation
  only from the requester named in the channel context, unless that requester
  explicitly delegates approval.

## MCP Tool Map

Hermes normally exposes these tools with an
`mcp_hedge_evaluation_control_` prefix:

- `evaluation_healthz`: check control-plane and worker health.
- `check_evaluation_environment_status`: inspect the deployed environment.
- `verify_evaluation_environment`: run post-deployment verification.
- `deploy_evaluation_environment`: submit the validated `broker_json` array.
- `get_evaluation_operation`: inspect one durable operation.
- `list_evaluation_operations`: find recent, queued, or running operations.
- `wait_evaluation_operation`: wait up to 30 seconds for an operation update.
- `cancel_evaluation_operation`: cancel an operation when supported.

Resolve the exact available prefixed name from the current MCP tool list. If
the MCP is unavailable, report the connection problem and stop; do not bypass
it.

## Route the Request

- For health, status, verification, or operation lookup, call the matching
  read-only tool directly. No deployment confirmation is needed.
- For deploy, install, or update requests, follow the Broker Collection and
  Deployment Workflow below.
- For cancellation, show the operation ID and current status, ask the requester
  for explicit cancellation confirmation, then call the cancel tool once.
- If a request mixes deployment with status questions, answer the read-only
  part while collecting the missing deployment data.

## Collect `broker.json`

Accept either a complete pasted JSON array or conversational field-by-field
input. If an attachment is not readable, ask the user to paste its JSON text.
When collecting interactively, ask one concise question at a time and preserve
all brokers, seats, endpoints, and trading fronts supplied by the user.

Validate this shape without inventing missing values:

```text
broker_json: array of broker
broker: {
  id: string,
  name: string,
  pinyin_index: string,
  seats: array of seat
}
seat: {
  id: string,
  name: string,
  sim: boolean,
  ctp_broker_id: string,
  ctp_app_id: string,
  ctp_auth_code: string,
  endpoints: array of endpoint
}
endpoint: {
  id: string,
  name: string,
  trading_fronts: array of string
}
```

Apply these checks before asking for confirmation:

1. The input is valid JSON with a top-level array.
2. Every required key exists and has the type shown above.
3. IDs, names, `pinyin_index`, CTP identifiers, auth codes, endpoint names,
   and trading-front strings are non-empty after trimming.
4. Broker IDs are unique. Seat IDs are unique within each broker. Endpoint IDs
   are unique within each seat.
5. Each broker has at least one seat, each seat has at least one endpoint, and
   each endpoint has at least one trading front.
6. Reject comments, trailing commas, placeholder secrets, and fields inferred
   from examples. Ask the user to correct them.

Do not treat an empty array as an ordinary deployment. Ask whether the user is
intentionally clearing all broker configuration, explain the impact, and still
require the normal separate confirmation if the control plane supports it.

## Confirm the Current Payload

After validation, show only a redacted summary:

- broker count and each broker's `id` and `name`;
- seat count and each seat's `id`, `name`, `sim`, and `ctp_broker_id`;
- endpoint IDs/names and trading-front counts;
- `ctp_app_id` masked to at most its last four characters;
- `ctp_auth_code` as `[已提供，已隐藏]` with no prefix or suffix revealed.

Then say that the fixed evaluation environment will be installed or updated
and ask the requester to reply exactly `确认部署测评环境`. The deployment request
must occur in a later user message; an initial “deploy this” message is not the
confirmation step.

Any edit, correction, added broker, or removed field invalidates the previous
confirmation. Revalidate, show a new redacted summary, and ask again. Never
reuse confirmation from another payload or another Slack user.

## Deployment Workflow

1. Call `evaluation_healthz`. Stop on an unhealthy registry or worker.
2. Check recent operations. If another deployment is queued or running, report
   its operation ID and ask whether to wait; do not submit a duplicate.
3. Collect and validate the complete `broker_json` array.
4. Show the redacted summary and obtain the separate confirmation above.
5. Call `deploy_evaluation_environment` exactly once with
   `broker_json=<validated array>`.
6. Record the returned operation ID. Do not resubmit merely because the
   operation is slow or a wait call times out.
7. Follow the durable operation with `wait_evaluation_operation` in bounded
   waits, then `get_evaluation_operation` when details are needed. Give concise
   progress updates in long-running Slack threads.
8. On success, call `verify_evaluation_environment` and report its result.
9. On failure, report the operation ID, terminal status, and sanitized error.
   Never include broker credentials or raw deployment arguments.

## Final Response

Always include the action performed, operation ID when available, current or
terminal status, and verification result. State clearly when no mutation was
performed. Keep the response in the Slack thread and never reproduce the full
`broker.json`.
