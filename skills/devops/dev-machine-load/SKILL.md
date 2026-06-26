---
name: dev-machine-load
description: Check configured development machine load with hermes devload.
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [devops, ssh, load, machines]
    related_skills: []
---

# Development Machine Load

Use this skill when the user asks about development machine load, which machine
is free, where to run tests, or whether the dev fleet is under pressure.

## Procedure

1. Run the devload command from the active Hermes checkout. Prefer the installed
   `hermes` command when it is available on `PATH`; in service/gateway contexts,
   use the checkout's venv entrypoint if the bare command is unavailable.

```bash
hermes devload --json || /home/user/Repos/hermes-agent/.venv/bin/hermes devload --json
```

2. Read the JSON results.
3. Summarize the useful answer in plain language:
   - call out machines with `status: "error"` first;
   - prefer machines with `status: "ok"` and the lowest `load1_per_cpu`;
   - mention memory pressure when `mem_used_pct` is high;
   - mention system disk pressure when `disk_free_gb` is low or `disk_used_pct` is high;
   - if all machines are busy, say that clearly.

Do not use a `/devload` slash command; this feature is exposed as the top-level
`hermes devload` CLI command. Do not add or request a model tool for this.
