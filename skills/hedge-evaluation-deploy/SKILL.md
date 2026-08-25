---
name: hedge-evaluation-deploy
description: Manage the fixed Hedge evaluation environment from Slack by collecting, validating, confirming, and submitting broker.json, Hedge V2 version selectors, and CTP API test mode through the hedge-evaluation-control MCP server. Use when a user asks to deploy, install, update, check, verify, inspect, wait for, retry, or cancel an evaluation-environment operation, including requests started with /hedge-evaluation-deploy.
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
- Deployment changes the shared evaluation environment. Treat `broker_json`,
  the effective Hedge V2 version selection, and `ctp_api_test_mode` as one
  deployment submission. Require a separate, explicit confirmation for that
  current validated submission before calling the deployment tool.
- In a Slack workflow thread, accept deployment or cancellation confirmation
  only from the requester named in the channel context, unless that requester
  explicitly delegates approval.

## MCP Tool Map

Hermes normally exposes these tools with an
`mcp_hedge_evaluation_control_` prefix:

- `evaluation_healthz`: check control-plane and worker health.
- `check_evaluation_environment_status`: inspect the deployed environment.
- `verify_evaluation_environment`: run post-deployment verification.
- `deploy_evaluation_environment`: submit the validated `broker_json` array,
  Hedge V2 version selectors, and `ctp_api_test_mode`.
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
- For deploy, install, or update requests, follow the collection and deployment
  workflow below.
- For cancellation, show the operation ID and current status, ask the requester
  for explicit cancellation confirmation, then call the cancel tool once.
- If a request mixes deployment with status questions, answer the read-only
  part while collecting the missing deployment data.

## Collect `broker.json`

In Slack, accept `broker.json` only from a message containing exactly one code
block whose complete content is a valid top-level JSON array. Do not deploy
from plain pasted JSON, slash-command arguments, attachments, conversational
field-by-field input, or a message containing multiple valid JSON-array code
blocks. Ask the requester to resubmit those inputs as one code block.

The Slack workflow extracts `rich_text_preformatted` content before mrkdwn
auto-linking. When the current message says it contains exactly one valid code
block and includes the `Authoritative broker_json` marker, use only the content
after that marker. Preserve it exactly: do not remove `<` or `>`, unwrap links,
reformat strings, merge a duplicate Slack rendering, or infer missing values.
If the authoritative content is invalid, report the real validation problem and
ask for a corrected single code block.

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

The MCP input schema does not guarantee that the control plane accepts an empty
array. Do not use a live deployment to probe this. If the user submits `[]`,
stop before confirmation, explain that empty-broker behavior requires an
explicit current server contract, and do not claim it will clear configuration
unless the server contract confirms that behavior.

## Collect Deployment Options

Collect version selectors and `ctp_api_test_mode` outside the authoritative
`broker.json` code block. Do not add these fields to the broker array or rewrite
the code-block content. Treat the effective values as part of the same current
deployment submission.

Use exactly one supported Hedge V2 version mode:

- omit `commit`, `branch`, and `tag` for the latest `origin/master`;
- provide `commit` alone for that commit on master;
- provide both `branch` and `commit` for that commit on the named branch; or
- provide `tag` alone for a numeric release tag.

Reject `branch` without `commit`, `tag` combined with either other selector, and
any other combination. Do not infer a missing selector or reinterpret an
ambiguous version string; ask the requester to choose one supported mode.

`ctp_api_test_mode` must be a JSON boolean, not a string. Its effective value is
`true` when the requester omits it. State that default explicitly before
confirmation. Preserve an explicit `false` value; never replace it with the
default through truthiness or omission.

## Confirm the Complete Submission

After validation, show only a redacted summary:

- broker count and each broker's `id` and `name`;
- seat count and each seat's `id`, `name`, `sim`, and `ctp_broker_id`;
- endpoint IDs/names and trading-front counts;
- `ctp_app_id` masked to at most its last four characters;
- `ctp_auth_code` as `[已提供，已隐藏]` with no prefix or suffix revealed;
- the effective version mode and selector values, or `latest origin/master`;
- the effective boolean `ctp_api_test_mode`, marking `true` as the default when
  the requester omitted it.

Then say that the fixed evaluation environment will be installed or updated
and ask the requester to reply exactly `确认部署测评环境`. The deployment request
must occur in a later user message; an initial “deploy this” message is not the
confirmation step.

Any broker edit or any change to `commit`, `branch`, `tag`, or
`ctp_api_test_mode` invalidates the previous confirmation. Revalidate the
complete submission, show a new redacted summary, and ask again. Never reuse
confirmation from another submission or another Slack user.

## Deployment Workflow

For a requested retry after a terminal failed deployment, read
`references/retry-after-failed-deploy.md` before taking action.

1. Call `evaluation_healthz`. Stop on an unhealthy registry or worker.
2. Check recent operations. If another deployment is queued or running, report
   its operation ID and ask whether to wait; do not submit a duplicate.
3. Collect the complete `broker_json` array from exactly one Slack code block
   and validate its authoritative content without rewriting it.
4. Resolve one supported version mode and the effective boolean
   `ctp_api_test_mode`.
5. Show the redacted summary of the complete deployment submission and obtain
   the separate confirmation above.
6. Call `deploy_evaluation_environment` exactly once with the validated
   `broker_json` and an explicit `ctp_api_test_mode`. For latest master, omit all
   version selectors; otherwise pass only the selector combination for the
   confirmed version mode.
7. Record the returned operation ID. Do not resubmit merely because the
   operation is slow or a wait call times out.
8. Follow the durable operation with `wait_evaluation_operation` in bounded
   waits, then `get_evaluation_operation` when details are needed. Give concise
   progress updates in long-running Slack threads.
9. On success, call `verify_evaluation_environment` and report its result.
10. On failure, report the operation ID, terminal status, and sanitized error.
   Never include broker credentials or raw deployment arguments.

## Delayed Follow-up Checks

When the requester asks to check a deployment later, create a one-shot cron job
that references only the operation ID, not the original `broker.json` or
credentials. Its prompt must use the `hedge-evaluation-control` MCP tools, get
the operation, run verification only after a successful terminal deploy, and
report a concise sanitized status to the originating Slack thread.

Do not restrict the cron job to a toolset that excludes MCP tools. Prefer
omitting `enabled_toolsets` unless the scheduler explicitly supports an
MCP/evaluation-control toolset.

## Final Response

Always include the action performed, operation ID when available, current or
terminal status, effective or pinned Hedge V2 version when known,
`ctp_api_test_mode` when known, and verification result. State clearly when no
mutation was performed. Keep the response in the Slack thread and never
reproduce the full `broker.json`.
