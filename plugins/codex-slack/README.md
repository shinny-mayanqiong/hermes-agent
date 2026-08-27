# Codex Slack

Controls local Codex app-server threads from the Slack channel that Hermes
already uses as its home channel.

## Configuration

Enable the plugin and keep behavioral settings in `config.yaml`:

```yaml
plugins:
  enabled:
    - codex-slack
  codex-slack:
    default_cwd: /home/user/Repos/odoo-hedge
    allowed_roots:
      - /home/user/Repos
    page_size: 20
    queue_limit: 5
    max_live_sessions: 5
    idle_timeout_seconds: 1800

slack:
  free_response_channels:
    - C0123456789
```

The target channel is **not** configured under the plugin. It is read from the
same Slack home-channel setting used for the gateway startup notification.
Run `/sethome` in the private channel to set it. Add that same channel to
`slack.free_response_channels` so replies to an existing Codex thread still
reach the plugin after a gateway restart. If `slack.allowed_channels` is
non-empty, it must include the home channel too.

Access is channel-scoped, not user-scoped: every member who can use the home
channel can list, create, resume, continue, stop, and approve its Codex
sessions. The stored Slack user ID is audit metadata only.

Regenerate and apply the Slack app manifest after enabling the plugin so Slack
knows about the native `/codex` command. Until then, every command below also
works through the existing `/hermes` entry point, for example
`/hermes codex sessions`.

## Commands

- `/codex sessions` — list local interactive Codex sessions, 20 per page.
- `/codex new [directory]` — create a session; the configured default is used
  when no directory is given. Relative paths are resolved from the first
  `allowed_roots` entry, so `team/project/worktree` means
  `/home/user/Repos/team/project/worktree` with the example configuration.
- `/codex resume <session-id> [new-directory]` — bind an existing Codex session
  to a new Slack thread. Supply a new directory when the session's original
  worktree no longer exists.
- `!status` — show status inside a bound thread.
- `!stop` — interrupt the active turn, clear queued messages, and close the
  local app-server process. The binding and Codex history remain, so the next
  message resumes the same session.

Only text input is supported in v1. Codex's own permission configuration stays
authoritative; approval requests appear as Slack buttons in the bound thread.
