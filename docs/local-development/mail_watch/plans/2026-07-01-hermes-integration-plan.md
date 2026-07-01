# Mail Watch Hermes Integration Plan

## 目标

把当前运行在 `/home/user/Repos/ops-scripts/mail_watch` 的邮箱监控脚本迁入
Hermes，使它成为可维护、可测试、可调度的 Hermes 边缘能力。

迁入后应保持现有生产行为不变：

- 继续监控当前 IMAP mailbox/folder。
- 继续使用现有规则匹配、dedupe、checkpoint、retry 和 SQLite state 语义。
- 继续支持当前 Slack webhook delivery。
- 迁移过程可回滚到现有 crontab 运行方式。

## 背景

当前 `mail_watch` 已在本机 `/home/user/Repos/ops-scripts` 中维护，并由本机
crontab 定时执行。它不是 Hermes core 能力，也不应该作为 core model tool
暴露给每次模型调用。

按照 Hermes 的 footprint ladder，推荐路径是：

1. 先作为 plugin/CLI 能力迁入。
2. 保留 legacy JSON config 与现有 SQLite state。
3. 再逐步迁到 Hermes config/profile/cron。

## 范围

本计划覆盖：

- 将 `mail_watch` 代码接入 Hermes repo 的方式。
- CLI command 和调度入口设计。
- 配置、secret、SQLite state 和 Slack delivery 的迁移边界。
- 每个阶段的验证方式和回滚方式。

本计划不覆盖：

- 修改邮件业务规则。
- 修改现有 Slack 消息内容语义。
- 更换 IMAP 账号或 mailbox/folder。
- 把 `mail_watch` 做成 Hermes core tool。
- 把 secret 值写入 repo 文档或代码。

## 当前事实

- 来源代码：`/home/user/Repos/ops-scripts/mail_watch`
- 来源配置：`/home/user/Repos/ops-scripts/configs/mail_watch/ops.json`
- 当前调度：本机 crontab
- 当前 state：SQLite database + lock file
- 当前 secret 输入：IMAP credential env vars + Slack webhook env vars
- Hermes 状态：尚未迁入

## 目标架构

推荐目标：

```text
Hermes CLI / Hermes cron
  -> mail_watch plugin command
    -> migrated mail_watch package
      -> IMAP scan
      -> rule match + analysis
      -> SQLite state/checkpoint
      -> Slack webhook delivery
```

后续可选增强：

```text
mail_watch plugin
  -> Hermes profile config
  -> Hermes notification abstraction or gateway delivery
```

## 阶段计划

### Phase 1 - 原样迁入 Hermes plugin

目标：先让代码进入 Hermes，但不改变业务行为。

步骤：

- 在 Hermes repo 中新增 `plugins/mail_watch/`。
- 原样迁入 `mail_watch` package 的核心模块。
- 增加 plugin metadata。
- 增加 CLI command，例如：
  - `hermes mail-watch run`
  - `hermes mail-watch validate`
  - `hermes mail-watch init-db`
  - `hermes mail-watch status`
- CLI 初期继续支持 `--config <legacy-json>`。

验证：

- `validate` 能读取现有 JSON config。
- `init-db` 能在临时路径创建 schema。
- `run --no-notify` 能完成扫描流程，不发送 Slack。
- 不改变现有 crontab。

回滚：

- 删除或禁用 Hermes plugin 即可。
- 现有 `/home/user/Repos/ops-scripts` crontab 继续运行。

### Phase 2 - 迁移测试覆盖

目标：把当前行为锁住，避免后续 Hermes 化时破坏生产语义。

步骤：

- 从 `ops-scripts` 迁入或重写 focused tests。
- 覆盖：
  - config validation
  - rule matching
  - initial scan / checkpoint
  - dedupe
  - notification event retry
  - IMAP config missing failure handling
  - DB size monitoring
- 测试使用临时 database 和 fake IMAP/Slack，不触碰生产 state。

验证：

- Hermes repo 下相关 pytest 通过。
- 测试断言行为关系和状态变化，不写纯 change-detector tests。

回滚：

- 测试阶段不影响生产运行。

### Phase 3 - Hermes config 适配

目标：支持 Hermes profile config，但保持 legacy JSON 可用。

步骤：

- 先支持 `config_path` 指向 legacy JSON，作为过渡形态：

```yaml
mail_watch:
  config_path: /home/user/Repos/ops-scripts/configs/mail_watch/ops.json
```

- 最终 Hermes config 结构应对齐当前 `ops.json` 的业务结构，而不是引入
  `accounts.ops` 这类提前抽象。示例：

```yaml
mail_watch:
  account: ops@example.com
  folder: "其他文件夹/ops"
  database_path: /home/user/Documents/mail_watch_data/mail_watch_notify.sqlite
  lock_file: /home/user/Documents/mail_watch_data/mail_watch_notify.lock
  db_size_alert_mb: 500
  body_preview_limit: 4000
  slack_retry_limit: 3
  imap_failure_notify_threshold: 5
  max_notify_per_run: 20
  critical_slack_webhook_url_env: MAIL_WATCH_CRITICAL_SLACK_WEBHOOK_URL
  critical_exclude_body_regex:
    - "IP Address:\\s*192\\.168\\.152\\.42"
  rules:
    - id: zhonghang-zhongqi-alert
      enabled: true
      match_mode: all
      case_sensitive: false
      from_regex:
        - "中航国际众期业务管理系统\\s*<zq@mail\\.4008207951\\.cn>"
      subject_regex: []
      body_regex: []
      exclude_body_regex:
        - "IP Address:\\s*192\\.168\\.152\\.42"
      dedupe_group: zhonghang-zhongqi
      slack_title: 中航国际邮件提醒
      slack_webhook_url_env: MAIL_WATCH_ZHONGHANG_SLACK_WEBHOOK_URL
```

- 如果未来确实需要多个 mailbox/rule set，再引入多实例结构。现在不要为单一
  `ops.json` 过早增加 `accounts` 层级。
- IMAP password、Slack webhook URL 等继续只通过 env vars 或外部 secret store 提供。

验证：

- legacy `--config` 路径可用。
- Hermes profile config 中的 direct `mail_watch` 结构可用。
- 文档只记录 env var 名，不记录值。

回滚：

- 切回 `--config` legacy JSON。

### Phase 4 - Delivery 边界抽象

目标：保留 Slack webhook，同时为未来 Hermes delivery 留出口。

步骤：

- 把通知发送从业务扫描流程中清晰隔离为 delivery adapter。
- 初始 adapter 仍是现有 Slack incoming webhook。
- 保持 critical webhook 和 per-rule webhook 的现有语义。
- 只在有明确需求后再接 Hermes notification/gateway delivery。

验证：

- webhook adapter 的 retry、error recording、sent marker 不变。
- fake sender 测试覆盖发送成功和失败路径。

回滚：

- 回到现有 notifier/slack 模块路径。

### Phase 5 - Hermes 调度接入

目标：让 Hermes 接管调度入口，而不是长期依赖裸 crontab。

步骤：

- 增加 `mail-watch install-cron` 或记录手工 `hermes cron` 配置方式。
- Cron 命令调用 Hermes CLI，例如：

```text
hermes mail-watch run --profile <profile> --account ops
```

- 初期可以使用 `--deliver local` 或 `--no-agent` 风格，避免每次扫描都启动模型。
- 确认 lock file 仍防止并发扫描。

验证：

- Hermes cron 能按周期触发。
- 日志位置明确。
- 旧 crontab 和新 Hermes cron 不会同时运行同一 production mailbox。

回滚：

- 禁用 Hermes cron。
- 恢复或保留旧 crontab。

### Phase 6 - 迁移切换与清理

目标：完成生产切换，并保留可审计记录。

步骤：

- 在文档中记录旧 crontab 内容、切换时间和新入口。
- 确认 production SQLite database 路径是否复用或迁移。
- 若迁移 database，先备份。
- 停用旧 crontab。
- 增加 status/runbook，记录如何检查：
  - 最近 checkpoint
  - pending notification events
  - failed notification events
  - last IMAP failure

验证：

- 连续多个调度周期无重复通知。
- checkpoint 正常前进。
- Slack 通知正常。
- error log 没有持续 IMAP/DB/Slack failure。

回滚：

- 停用 Hermes cron。
- 恢复旧 crontab。
- 使用备份 database 或原 database 路径。

## 风险

- Initial scan 语义如果处理不当，可能补发大量历史邮件通知。
- 同时启用旧 crontab 和 Hermes cron，可能造成并发或重复发送。
- 迁移 SQLite database/checkpoint 时如果路径错误，可能重新扫描旧邮件。
- Slack webhook 或 IMAP secret 不能写入文档或 repo。
- 过早重构业务规则会扩大验证范围。

## 决策点

- `mail_watch` 最终是 bundled plugin 还是只作为 local plugin 维护。
- Hermes config 过渡期是否只引用 legacy JSON；最终结构按 `ops.json` 形状迁移。
- Slack delivery 是否长期保留 webhook，还是后续接 Hermes notification/gateway。
- 是否需要多 account/multi-folder 支持；只有出现真实需求后才增加多实例层级。
- 是否要为 first-run 增加显式策略，例如 `process_existing` 或 `skip_existing`。

## 下一步

先执行 Phase 1 和 Phase 2：

1. 新增 `plugins/mail_watch/`。
2. 保持 legacy JSON config 可用。
3. 迁入 focused tests。
4. 验证 Hermes CLI 路径和现有 `ops-scripts` 路径行为一致。
