# Read-only SSH evidence collection

Use this workflow when the user requests existing evaluation logs, identifies
an account or order to investigate, or needs original evidence for a test
report. This is the permitted SSH exception to the deployment-only MCP rule.
A request to collect logs is sufficient authorization for these read-only
steps; do not ask again for deployment confirmation.

## Connection and scope

The fixed target is `192.168.139.244`, SSH port `22`, user `user`. The existing
evaluation runner uses `/home/user/.ssh/hedge_mayanqiong`. Use that identity
when available; never read, print, or export the private key. Connect with an
explicit user: the local `debian-244` alias may contain a different username.

```sh
ssh -T -o BatchMode=yes -o ConnectTimeout=10 -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -i /home/user/.ssh/hedge_mayanqiong user@192.168.139.244
```

Read existing systemd metadata, journal entries, log files, and the minimum
database records needed to establish account identity, orders, or alerts.
Database queries must run in an explicitly read-only transaction. Use
`sudo -n` only where existing read permissions require it. Do not install
packages, edit configuration, start/stop/restart services or runtimes, change
logging, trigger alerts, or submit/cancel orders. Report permission failures
without changing permissions or weakening SSH host-key checks.

Export through stdout, SFTP, or SCP into a local evidence directory. Do not
create archives or temporary files on the remote machine. Do not copy
`broker.json`, full runtime env files, private keys, passwords, or tokens.
Read only named non-secret fields when configuration is needed for identity
or log-path discovery. Avoid full process command lines or environments that
can contain credentials.

## Establish identity and find retained sources

1. Record hostname, collection time/time zone, and the fixed target address.
2. Match the requested futures username to the runtime env identity fields
   `HEDGE_TD_RUNTIME_FUTURE_USERNAME`, `HEDGE_TD_RUNTIME_ACCOUNT_ID`,
   `HEDGE_TD_RUNTIME_HEDGE_DB_NAME`, `HEDGE_TD_RUNTIME_RUNTIME_KEY`, and
   `HEDGE_TD_RUNTIME_UNIT_NAME`. Read only these and the log path/attempt ID;
   never export the entire env file. If necessary, confirm the account in a
   read-only database query. A gateway PID is not an account runtime identity.
3. Resolve actual log locations from the gateway config's `logging.log_file`
   and `runtime.log_dir`, plus the matching runtime's
   `HEDGE_TD_RUNTIME_LOG_FILE`. Check compressed/rotated logs as well as the
   current file. Runtime keys use the database name and internal account ID,
   so a filename need not contain the futures username.
4. Inspect the matched runtime unit and existing TD, ZQ, Odoo, and journal
   sources for the requested time range. Use explicit time zones; Odoo stored
   datetimes can be UTC even when the report requests Beijing time.

Paths observed on this deployment include `/var/log/ef/hedge-td-runtime/`
and `/var/log/ef/hedge-td-gateway/root.jsonl`. Confirm live configuration
rather than treating these paths or an old PID as permanent facts.

## Extract and deliver genuine evidence

- Normal orders: correlate `Placing request`, submission, and
  `Tracked order snapshot` by request/order ID. Preserve instrument, direction,
  price, volume, original timestamps, and rejection messages where present.
- Initialization: locate `tqsdk connection initializing`, initialized, or
  `Starting order sync loop` records for the matched runtime/attempt.
- Monitoring: export actual alert records with occurrence time, trading day,
  period, metric, observed value, and threshold, plus related logs. Existing
  alerts may be in `hedge.risk.alert`; routine service logs alone do not prove
  an alert occurred.
- Errors: correlate the requested order/request with its real rejection or
  `last_msg`, including nearby TD/ZQ records. A similar error from another
  order is not evidence for the requested event.

Preserve continuous lines around each event and original ordering. Export
TXT/JSONL/CSV files and an index giving source path, source line numbers,
artifact line numbers, event identifiers, time zone, and any redactions.
For compressed sources, line numbers refer to decompressed content. Hash
exported files when useful for handing evidence to the evaluating firm.

Check selected output for credentials before delivery. If redaction is
necessary, label the file as a redacted excerpt and mark changed values;
never describe altered content as an unmodified original. Keep only the
requested account's evidence in the external handoff. Downloaded diagnostic
archives, especially TQSdk wire logs, can contain credentials and require
inspection before sharing.

Honor a requested context length where retained records support it. If fewer
lines or no matching event exist, report actual counts, checked sources, and
the specific gap. Do not synthesize records, trigger a new event, or pad a
snippet to meet a line target.

## Loading the updated policy

Gateway auto-loads this skill only at the start of a new session. Updating
the deployed skill does not rewrite an existing conversation. Start a new
evaluation thread/session, or explicitly load the updated skill in the old
thread as a new user instruction. Preserve previous messages and the cached
system prompt; no gateway restart is needed for a fresh session.
