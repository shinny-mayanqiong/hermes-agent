# Slack Socket Forwarder

Opt-in Hermes plugin for forwarding selected Slack Socket Mode Block Kit actions
to an internal HTTP endpoint.

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
