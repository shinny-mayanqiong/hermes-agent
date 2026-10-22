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
      - /home/user/Repos/odoo-hedge
      - /home/user/Repos/odoo-hedge-worktrees
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
knows about the native `/codex` and `/odoo-hedge-codex` commands. Until then, every command below also
works through the existing `/hermes` entry point, for example
`/hermes codex sessions` or `/hermes odoo-hedge-codex`.

To show `/odoo-hedge-codex` in Slack's `/` menu, add it to the installed app's
**Slash Commands** (or merge its generated `features.slash_commands` entry
into the existing App Manifest). Set its description to
“Odoo Hedge：查看、创建或继续 Codex 会话” and its usage hint to
`[sessions [archived] | new [dir] | resume id [dir]]`.
Save and reinstall if Slack prompts. Socket Mode delivers the command to the
existing gateway; no new public endpoint is needed. Use the channel composer,
since Slack does not support custom slash commands inside message threads.

## Commands

- `/odoo-hedge-codex` — open the project session cards directly. It also accepts
  every `/codex` subcommand below, including `new`, `resume`, and `sessions archived`.
- `/codex sessions` — list unarchived project sessions, 20 per page.
- `/codex sessions archived` — list archived project sessions. The list also
  has a button to switch between archived and unarchived views.
- `/codex new [directory]` — create a session; the configured default is used
  when no directory is given. Relative paths are resolved from the first
  `allowed_roots` entry. Use an absolute path for a worktree under another root.
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

## Session cards

The ephemeral list shows 20 compact cards per page. Each card contains the ID
(last four characters) and title, followed by creation time, update time, and
the full path on separate lines. Text wraps in a single column; Resume / Archive /
Delete sit in a separate action row below the card. There is no status column
or horizontal table scrolling. Times include seconds and use the gateway host's
local timezone, shown above the list. Only long titles are abbreviated; paths
and timestamps are preserved in full. Exceptionally long paths use additional
text sections, splitting a logical page across messages if needed to stay within
Slack's block limits while keeping each card with its buttons.
Buttons retain the full ID internally, so matching four-character suffixes do
not select the wrong session.

The list filters to `allowed_roots` and their descendants before filling each
page. It excludes sibling prefixes and symlinks pointing outside the roots.
Removed worktrees remain listed so their sessions can be archived, deleted, or
resumed with a replacement directory. The same scope applies to new sessions,
resuming by ID, and continued work in existing bindings.

Resume opens the original Slack thread if one already exists. In the archived
view it first restores the Codex session. Archive pauses mirroring and preserves
the binding and delivery checkpoints. Delete permanently removes Codex history;
the original Slack messages and project files remain. Both actions use Slack
confirmation dialogs explaining that Codex also processes spawned descendants.
Active sessions must be stopped before archiving or deleting. Failed operations
preserve the binding state; successful mutations invalidate old row buttons and
refresh the list. The archived view offers Resume and Delete.

Protocol references: [Codex App Server](https://learn.chatgpt.com/docs/app-server)
and [Slack sections](https://docs.slack.dev/reference/block-kit/blocks/section-block/)
with [action rows](https://docs.slack.dev/reference/block-kit/blocks/actions-block/).
