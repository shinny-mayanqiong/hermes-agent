# Retry after failed evaluation deployment

Read this reference only when a requester asks to retry after a prior deploy
operation reached a terminal failure.

## Safe retry checklist

1. Confirm that the prior operation is terminal. Never duplicate a queued or
   running deployment.
2. Confirm that the request comes from the same requester in the same Slack
   workflow thread.
3. Compare the complete confirmed deployment submission: `broker_json`,
   `commit`, `branch`, `tag`, and `ctp_api_test_mode`. A changed value is a new
   submission, not a retry; revalidate it, show a new redacted summary, and ask
   for `确认部署测评环境` again.
4. Re-run `evaluation_healthz` and stop if the registry or worker is unhealthy.
5. Check for queued or running deploy operations. If one exists, report it and
   do not submit another.
6. Submit exactly one new deploy operation with the unchanged complete
   submission and record the new operation ID separately from the failed one.
7. Follow the new operation with bounded waits. Do not resubmit while it is
   queued or running, and verify only after success.

Report only sanitized failure details. Never reproduce broker credentials or
full deployment arguments. It is safe to report operation IDs, status, step,
effective or pinned version, `ctp_api_test_mode`, and sanitized error details.
