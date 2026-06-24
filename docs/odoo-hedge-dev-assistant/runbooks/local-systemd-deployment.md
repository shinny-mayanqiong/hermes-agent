# 本机 user systemd 部署 Runbook

## 用途

本 runbook 记录当前开发机器上 `odoo-hedge` 开发辅助 Hermes 的实际部署方式。

截至 2026-06-24，本机 `/home/user/Repos/hermes-agent` 是实际运行
`odoo-hedge-dev` Hermes gateway 和 dashboard 的机器。gateway 与 dashboard
都以当前 Linux 用户的 `systemd --user` unit 运行，不是一次性前台进程。

## 部署范围

Hermes 仓库：

```text
/home/user/Repos/hermes-agent
```

Python venv：

```text
/home/user/Repos/hermes-agent/.venv
```

profile：

```text
odoo-hedge-dev
```

profile `HERMES_HOME`：

```text
/home/user/.hermes/profiles/odoo-hedge-dev
```

systemd user units：

```text
hermes-odoo-hedge-dev.target
hermes-gateway-odoo-hedge-dev.service
hermes-dashboard-odoo-hedge-dev.service
```

unit 文件位置：

```text
/home/user/.config/systemd/user/hermes-odoo-hedge-dev.target
/home/user/.config/systemd/user/hermes-gateway-odoo-hedge-dev.service
/home/user/.config/systemd/user/hermes-dashboard-odoo-hedge-dev.service
```

## 当前启动命令

Gateway service：

```text
/home/user/Repos/hermes-agent/.venv/bin/python -m hermes_cli.main --profile odoo-hedge-dev gateway run --replace
```

Dashboard service：

```text
/home/user/Repos/hermes-agent/.venv/bin/hermes -p odoo-hedge-dev dashboard --tui --skip-build --no-open --host 0.0.0.0 --port 9119 --insecure
```

两个 service 当前 `WorkingDirectory` 均为：

```text
/home/user/.hermes/profiles/odoo-hedge-dev
```

Gateway service 还加载环境文件：

```text
/home/user/.config/hermes/odoo-hedge-github.env
```

不要把该文件内容写入仓库文档。文档中只记录路径和用途。

Dashboard public bind 通过 `dashboard.basic_auth` 保护。当前 auth 配置写在：

```text
/home/user/.hermes/config.yaml
/home/user/.hermes/profiles/odoo-hedge-dev/config.yaml
```

本机保存了一份登录凭据，权限应保持为 `0600`：

```text
/home/user/.config/hermes/odoo-hedge-dashboard-basic-auth.txt
```

## 状态检查

检查 stack：

```bash
systemctl --user status hermes-odoo-hedge-dev.target
```

检查 gateway：

```bash
systemctl --user status hermes-gateway-odoo-hedge-dev.service
```

检查 dashboard：

```bash
systemctl --user status hermes-dashboard-odoo-hedge-dev.service
```

列出 Hermes 相关 user units：

```bash
systemctl --user list-units '*hermes*' --all --no-pager
systemctl --user list-unit-files '*hermes*' --no-pager
```

预期当前三个 unit 均为 `enabled`，运行态为：

```text
hermes-odoo-hedge-dev.target            active
hermes-gateway-odoo-hedge-dev.service   active/running
hermes-dashboard-odoo-hedge-dev.service active/running
```

注意：`hermes profile show odoo-hedge-dev` 的 `Gateway` 字段可能不识别当前
自定义 unit 名称，不能作为本机部署状态的事实源。本机 gateway/dashboard
运行状态以 `systemctl --user` 为准。

## 日志

Gateway journal：

```bash
journalctl --user -u hermes-gateway-odoo-hedge-dev.service -f
```

Dashboard journal：

```bash
journalctl --user -u hermes-dashboard-odoo-hedge-dev.service -f
```

最近 200 行：

```bash
journalctl --user -u hermes-gateway-odoo-hedge-dev.service -n 200 --no-pager
journalctl --user -u hermes-dashboard-odoo-hedge-dev.service -n 200 --no-pager
```

## 重启

Hermes 代码更新、rebase、plugin 更新、gateway command 更新后，重启两个
service 让新 Python 代码生效：

```bash
systemctl --user restart hermes-gateway-odoo-hedge-dev.service
systemctl --user restart hermes-dashboard-odoo-hedge-dev.service
```

也可以一次重启：

```bash
systemctl --user restart \
  hermes-gateway-odoo-hedge-dev.service \
  hermes-dashboard-odoo-hedge-dev.service
```

如果只改 gateway 相关代码，只重启 gateway 即可；如果 dashboard 或 embedded
TUI 相关代码也变更，同时重启 dashboard。

## 停止和启动

停止 gateway：

```bash
systemctl --user stop hermes-gateway-odoo-hedge-dev.service
```

停止 dashboard：

```bash
systemctl --user stop hermes-dashboard-odoo-hedge-dev.service
```

启动 gateway：

```bash
systemctl --user start hermes-gateway-odoo-hedge-dev.service
```

启动 dashboard：

```bash
systemctl --user start hermes-dashboard-odoo-hedge-dev.service
```

## 修改 unit 后

修改 `~/.config/systemd/user/*.service` 或 drop-in 后执行：

```bash
systemctl --user daemon-reload
systemctl --user restart hermes-gateway-odoo-hedge-dev.service
systemctl --user restart hermes-dashboard-odoo-hedge-dev.service
```

## Dashboard 绑定与安全边界

Dashboard 当前使用 `--host 0.0.0.0 --port 9119 --insecure`，用于内网访问。
当前 Hermes 版本中，`--insecure` 不再绕过非 loopback dashboard 的 auth gate；
因此这个 public bind 必须同时配置 `dashboard.basic_auth` 或其他
DashboardAuthProvider。

如果移除 `dashboard.basic_auth` 且继续绑定 `0.0.0.0`，dashboard 会拒绝启动并
进入 systemd 重启循环。仅在确认要改成本机隧道访问时，才切换为
`--host 127.0.0.1`。
