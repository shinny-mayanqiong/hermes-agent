# Mail Watch

本目录记录 `mail_watch` 从 `/home/user/Repos/ops-scripts` 迁入 Hermes 的设计、
盘点、计划和运维文档。

`mail_watch` 当前职责是监控指定 IMAP 邮箱文件夹，按规则匹配业务邮件，记录
SQLite 状态，并把通知转发到 Slack。当前本机仍通过 crontab 直接运行
`/home/user/Repos/ops-scripts/mail_watch`。

## 当前状态

- 来源代码：`/home/user/Repos/ops-scripts/mail_watch`
- 来源配置：`/home/user/Repos/ops-scripts/configs/mail_watch/ops.json`
- 本机调度：crontab
- 状态存储：SQLite database 和 lock file
- secret 边界：IMAP password 与 Slack webhook URL 只通过环境变量提供
- Hermes 状态：待迁入

## 目标形态

推荐目标是把 `mail_watch` 作为 Hermes 边缘能力接入：

- bundled plugin 或 local plugin 承载代码
- CLI command 提供 `run`、`validate`、`init-db`、`status` 等入口
- Hermes cron 或等价调度替代裸 crontab
- 先兼容现有 JSON 配置，再逐步迁到 Hermes profile config
- 保留 Slack webhook delivery 兼容路径，后续再决定是否接入 Hermes notification
  abstraction

不建议把 `mail_watch` 做成 Hermes core model tool。它是后台监控/通知能力，
不是每次模型调用都需要暴露的通用工具。

## 文档规划

后续可按需要增加：

- `inventory/`：记录当前代码、配置、crontab、数据库 schema 和环境变量名。
- `plans/`：记录迁入 Hermes 的分阶段计划和验证方式。
- `runbooks/`：记录日常运行、排障、回滚和 checkpoint 处理。

当前计划：

- `plans/2026-07-01-hermes-integration-plan.md` - `mail_watch` 迁入 Hermes
  的阶段计划、验证方式、风险和回滚边界。

## 维护边界

- 文档可以记录环境变量名，但不能记录实际 secret 值。
- 迁移前不要改变现有邮件扫描、规则匹配、dedupe、checkpoint 和 retry 语义。
- 涉及生产数据库或 checkpoint 的操作必须写清楚回滚方式。
- 代码迁入 Hermes 后，配置和调度说明应同步更新本目录文档。
