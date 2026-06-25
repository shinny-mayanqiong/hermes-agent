# Slack Socket Forwarder

Opt-in Hermes plugin for forwarding selected Slack Socket Mode Block Kit actions
to an internal HTTP endpoint.

## Current deployment purpose

This plugin is used by the `zq_hedge` Sentry autofix flow.

1. Sentry issues are sent to `zq_hedge_autofix_bot`.
2. `zq_hedge_autofix_bot` analyzes the issue and posts a Slack message with two
   Block Kit action buttons: `create_issue` and `create_issue_and_pr`.
3. The Hermes Slack app/gateway is the Socket Mode and interactivity entrypoint,
   so button clicks arrive at Hermes first.
4. This plugin registers handlers for those button action IDs and forwards a
   compact JSON payload back to `zq_hedge_autofix_bot`.
5. `zq_hedge_autofix_bot` performs the follow-up action: create an issue or
   queue a PR repair.

Implementation note: current Hermes plugins should register Slack Block Kit
action handlers with `ctx.register_slack_action_handler(action_id, callback)`.
Do not monkey-patch `SlackAdapter` or import adapter internals directly.

Default behavior:

- Action IDs: `create_issue`, `create_issue_and_pr`
- Target URL: `http://192.168.139.8:9000/internal/slack/socket-interactions`

Enable the plugin:

```yaml
plugins:
  enabled:
    - slack-socket-forwarder
```

Optional environment overrides:

```bash
SLACK_SOCKET_FORWARD_URL="http://192.168.139.8:9000/internal/slack/socket-interactions"
SLACK_SOCKET_FORWARD_ACTION_IDS="create_issue,create_issue_and_pr"
SLACK_SOCKET_FORWARD_TIMEOUT_SECONDS="10"
```

Forwarded JSON body:

```json
{
  "action_id": "create_issue",
  "incident_id": "...",
  "user_id": "U...",
  "channel_id": "C...",
  "message_ts": "..."
}
```
