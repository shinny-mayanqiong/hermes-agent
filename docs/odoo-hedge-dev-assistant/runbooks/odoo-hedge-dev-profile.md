# `odoo-hedge-dev` Profile Runbook

## 用途

本 runbook 记录如何检查和使用 Hermes 的 `odoo-hedge-dev` profile。

该 profile 面向开发人员，用于把 Hermes 作为
`/home/user/Repos/odoo-hedge` 的开发助手。

## 前置条件

Hermes 仓库位于：

```text
/home/user/Repos/hermes-agent
```

Hermes Python venv 位于：

```text
/home/user/Repos/hermes-agent/.venv
```

profile 已创建：

```text
odoo-hedge-dev
```

profile 路径：

```text
/home/user/.hermes/profiles/odoo-hedge-dev
```

alias 路径：

```text
/home/user/.local/bin/odoo-hedge-dev
```

## 检查 Hermes CLI

```bash
cd /home/user/Repos/hermes-agent
source .venv/bin/activate

which hermes
hermes --version
```

预期：

```text
/home/user/Repos/hermes-agent/.venv/bin/hermes
Hermes Agent v0.15.1
```

## 查看 profile

```bash
cd /home/user/Repos/hermes-agent
source .venv/bin/activate

hermes profile list
hermes profile show odoo-hedge-dev
```

预期：

- `odoo-hedge-dev` 出现在 profile list 中。
- model 显示为 `gpt-5.5 (openai-codex)`。
- alias 显示为 `/home/user/.local/bin/odoo-hedge-dev`。

## 待完成配置

当前 `odoo-hedge-dev` profile 仍需确认并设置：

```yaml
terminal:
  cwd: /home/user/Repos/odoo-hedge
```

不要把业务用户使用的 `~/.hermes/skills/domain/odoo-hedge*` skills 直接作为
开发 workflow 入口。开发人员 workflow 应独立建立。

## 使用方式

如果 alias 在 PATH 中可用：

```bash
odoo-hedge-dev
```

如果 alias 不在 PATH 中，先使用 venv 中的 Hermes CLI：

```bash
cd /home/user/Repos/hermes-agent
source .venv/bin/activate
hermes -p odoo-hedge-dev
```

如果 profile 参数行为变化，使用：

```bash
hermes profile use odoo-hedge-dev
hermes
```

## 验证工作目录

启动 profile 后，让 Hermes 执行只读检查：

```text
请运行 pwd，并确认当前目录是否为 /home/user/Repos/odoo-hedge。
```

预期：

```text
/home/user/Repos/odoo-hedge
```

如果不是，修正 profile 的 `terminal.cwd`。

## 常见问题

### `odoo-hedge-dev` 命令不存在

检查 alias 是否存在：

```bash
ls -l /home/user/.local/bin/odoo-hedge-dev
```

检查 PATH 是否包含：

```text
/home/user/.local/bin
```

### profile 启动后仍在 Hermes 仓库

检查：

```bash
sed -n '1,120p' /home/user/.hermes/profiles/odoo-hedge-dev/config.yaml
```

确认 `terminal.cwd` 是否仍为 `.`。

### Codex runtime 不稳定

先确认基础 Codex CLI：

```bash
which codex
codex --version
```

如果后续启用 `codex_app_server`，需要单独记录验证结果。

### `openai-codex` 返回 HTTP 401 / `token_expired`

现象示例：

```text
HTTP 401
token_expired
Provided authentication token is expired. Please try signing in again.
```

修复步骤：

```bash
cd /home/user/Repos/hermes-agent
source .venv/bin/activate

hermes -p odoo-hedge-dev auth logout openai-codex
hermes -p odoo-hedge-dev auth add openai-codex
```

按终端提示完成浏览器或 device code 登录。

验证：

```bash
hermes -p odoo-hedge-dev auth status openai-codex
```

如果仍然 401，列出 credential pool：

```bash
hermes -p odoo-hedge-dev auth list
```

然后移除旧的 `openai-codex` OAuth 条目，再重新 `auth add openai-codex`。
注意所有认证命令都要带 `-p odoo-hedge-dev`，否则可能修的是 default profile。
