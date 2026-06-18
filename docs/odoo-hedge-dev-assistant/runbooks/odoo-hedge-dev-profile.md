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

## profile 运行约定

所有 `odoo-hedge-*` 开发 profiles 必须保持以下约定。新增 profile 后，
需要按同样方式补齐。

### 工作目录

`config.yaml` 中必须设置：

```yaml
terminal:
  cwd: /home/user/Repos/odoo-hedge
```

### Git commit 作者

Hermes worker 使用每个 profile 的隔离 HOME：

```text
~/.hermes/profiles/<profile>/home/
```

因此 git commit 作者应写入对应 profile 的 `home/.gitconfig`，不要依赖
用户主目录的 `~/.gitconfig`。

当前统一身份：

```text
Hermes Hedge Agent <hermes-hedge-agent@users.noreply.github.com>
```

设置命令模板：

```bash
profile=odoo-hedge-coder
home="/home/user/.hermes/profiles/$profile/home"

mkdir -p "$home"
git config --file "$home/.gitconfig" user.name "Hermes Hedge Agent"
git config --file "$home/.gitconfig" user.email "hermes-hedge-agent@users.noreply.github.com"
chmod 700 "$home"
chmod 600 "$home/.gitconfig"
```

### GitHub CLI 登录

Hermes worker 的 `gh` 也读取 profile 隔离 HOME 下的配置：

```text
~/.hermes/profiles/<profile>/home/.config/gh/hosts.yml
~/.hermes/profiles/<profile>/home/.config/gh/config.yml
```

如果要让 worker 能创建 PR，需要给每个相关 profile 安装 GitHub CLI 登录
配置。仅复制 `~/.config/gh` 不一定足够，因为 `gh` token 可能来自系统
credential store。当前做法是在 profile 隔离 HOME 中写入可直接使用的
plain text `gh` token：

```bash
profile=odoo-hedge-coder
home="/home/user/.hermes/profiles/$profile/home"

mkdir -p "$home/.config/gh"
gh auth token | HOME="$home" gh auth login -h github.com --with-token --insecure-storage
chmod 700 "$home" "$home/.config" "$home/.config/gh"
chmod 600 "$home/.config/gh/hosts.yml" "$home/.config/gh/config.yml"
```

注意：`GH_TOKEN` 在 Hermes terminal 子进程环境中属于安全 blocklist，
即使写入 `terminal.env_passthrough` 也不会透传。不要把 `GH_TOKEN`
passthrough 当成 GitHub CLI 认证方案。

### PR 创建者

commit 作者由 `.gitconfig` 决定；PR 页面显示的 `opened by` 由 `gh auth`
登录账号决定。当前 PR 仍会显示为当前 `gh` 登录账号创建。若未来需要 PR
显示独立账号，需要使用 GitHub machine user / bot account，并把该账号的
`gh` 登录配置安装到各 profile 的隔离 HOME。

### 验证

对任一 profile，可用以下命令让 Hermes worker 自检：

```bash
cd /home/user/Repos/hermes-agent
source .venv/bin/activate

hermes -p odoo-hedge-coder chat -Q -q \
  "只运行并汇报：git config --global --get user.name; git config --global --get user.email; gh auth status"
```

预期：

- git user name 为 `Hermes Hedge Agent`。
- git user email 为 `hermes-hedge-agent@users.noreply.github.com`。
- `gh auth status` 显示已登录。

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
