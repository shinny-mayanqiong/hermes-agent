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
    shared_daemon: true
    daemon_socket: /home/user/.codex/app-server-control/app-server-control.sock
    progress_poll_seconds: 5

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

With `shared_daemon: true`, Hermes connects directly to the managed app-server's
Unix-socket WebSocket transport instead of spawning a separate app-server
writer. Start the managed daemon with
`codex app-server daemon bootstrap --remote-control`, then use `codex agents`
for the terminal UI. A plain `codex` process remains standalone and cannot
share a live session with Slack.

Bound sessions are monitored automatically when the gateway starts. Terminal
turns update a progress message in the same Slack thread and post their final
answer there. Polling reads the daemon's status/history without starting a model
turn. Delivery checkpoints live beside the bindings in the profile's SQLite
store, so a gateway restart catches up missed turns and avoids replaying answers
already delivered. Temporary Slack or daemon failures are retried. As with any
remote delivery, a process crash between sending an answer and saving its
checkpoint can cause that answer to be delivered again.

The polling interval is a minimum delay between scans; a large catch-up or a
slow daemon can take longer. Only sessions explicitly bound with `new` or
`resume` are mirrored. Old history is not bulk-posted when a session is first
bound; `!status` shows the most recent progress.

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
- `!status` — read current daemon status and recent progress inside a bound
  thread, including work started from the terminal.
- `!stop` — interrupt the active turn, clear queued messages, and close the
  local app-server process. The binding and Codex history remain, so the next
  message resumes the same session.

In shared-daemon mode `!stop` also interrupts a turn started from the terminal;
it disconnects Hermes' client without stopping the shared daemon. A gateway
shutdown disconnects its clients without interrupting daemon-owned work.

Codex only persists a new session after its first user message. Send the first
prompt in the new Slack thread before restarting the daemon or gateway; the
plugin keeps the creating connection alive until that prompt arrives.

## Quick check

1. Start the gateway with the profile containing these settings (for example,
   `hermes -p odoo-hedge-dev gateway run`).
2. In the home channel, run `/hermes codex new`, then reply with a task in the
   created thread. Approvals, progress, and the answer stay in that thread.
3. Run `/hermes codex sessions` or `/hermes codex resume <session-id>`. Reusing
   an already bound session links to its existing Slack thread.
4. Open that session using `codex agents` against the same local daemon and
   continue working. Slack receives subsequent progress and answers without
   another Slack prompt; `!status` reads live state and `!stop` interrupts it.
5. Restart only the Hermes gateway and verify that the binding and delivery
   checkpoint continue to work.

Only text input is supported in v1. Codex's own permission configuration stays
authoritative; approval requests appear as Slack buttons in the bound thread.
