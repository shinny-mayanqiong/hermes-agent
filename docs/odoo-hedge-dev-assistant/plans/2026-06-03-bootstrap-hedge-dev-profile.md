# 2026-06-03 Bootstrap `odoo-hedge` Dev Profile

## 目标

建立一个面向开发人员的 Hermes 使用入口，让 Hermes 默认作为
`/home/user/Repos/odoo-hedge` 的开发助手运行。

> 2026-06-24 状态更新：本 bootstrap 计划的基础目标已经完成。
> 当前本机 gateway/dashboard 部署事实见
> `../runbooks/local-systemd-deployment.md`，当前 profile 状态见
> `../runbooks/odoo-hedge-dev-profile.md`。

## 背景

以下为 2026-06-03 初始背景。默认 Hermes home 当时已经使用：

- `model.provider: openai-codex`
- `model.default: gpt-5.5`

Codex CLI 已可用：

- `codex-cli 0.136.0`

2026-06-03 当时仍存在几个缺口：

- 当时的 shell 中 `hermes` 命令不可见。
- 默认 Hermes home 的 `terminal.cwd` 当时仍是 `.`，尚未指向 `odoo-hedge`。
- `skills.external_dirs` 为空。
- 已有 `~/.hermes/skills/domain/odoo-hedge*` skills 面向业务用户，不是开发人员
  使用的开发 workflow。
- `odoo-hedge/.cursor/skills` 暂时忽略。
- 当前开发主力是 Codex，`.codex` 资产才是开发工作流主要输入。

## 范围

本计划只处理 Hermes 作为开发助手的基础接入。

包括：

- 让 `hermes` 命令在当前开发 shell 中可用
- 创建或配置 `odoo-hedge` 专用 Hermes profile
- 设置工作目录
- 确认 Codex 主力模型
- 初步接入 `.codex` 开发工作流资产
- 明确业务用户 skills 不作为开发 workflow 入口

不包括：

- 配置 Slack gateway
- 配置 web dashboard
- 配置 API Server
- 迁移所有 `.codex` skills
- 修改 Hermes core
- 修改 `odoo-hedge` 业务代码

## 前置条件

- 当前工作目录：`/home/user/Repos/hermes-agent`
- 目标开发仓库：`/home/user/Repos/odoo-hedge`
- Codex CLI 可用
- `odoo-hedge` 仓库本身遵循其根目录 `AGENTS.md` 和
  `hedge_docs/README.md`

## 阶段 1：确认 Hermes CLI 可用性

状态：已完成基础验证。

已确认：

```text
/home/user/Repos/hermes-agent/.venv/bin/hermes
Hermes Agent v0.17.0 (2026.6.19)
```

### 操作

确认 `hermes` 命令来源：

```bash
command -v hermes
python -m pip show hermes-agent
```

如果 `hermes` 不在 PATH，确认是否从源码运行：

```bash
python -m hermes_cli.main --help
python -m pip install -e .
```

具体命令需根据当前 venv / install 状态确认后执行。

### 验证

```bash
hermes --version
hermes --help
```

预期：

- `hermes` 命令可运行。
- 版本输出正常。

## 阶段 2：建立 `odoo-hedge` 专用 profile

状态：已创建，用户已确认工作目录配置完成。

已确认：

```text
Profile: odoo-hedge-dev
Path:    /home/user/.hermes/profiles/odoo-hedge-dev
Model:   gpt-5.5 (openai-codex)
Alias:   /home/user/.local/bin/odoo-hedge-dev
```

当前结论：

- `terminal.cwd` 已由用户确认配置完成。
- 下一步进入多角色 agent 编排设计与迁移。

### 操作

使用已创建的 profile：

```text
odoo-hedge-dev
```

配置目标：

```yaml
model:
  provider: openai-codex
  default: gpt-5.5

terminal:
  backend: local
  cwd: /home/user/Repos/odoo-hedge
```

### 验证

启动 Hermes 后确认：

- 当前工作目录是 `/home/user/Repos/odoo-hedge`
- 模型 provider 是 `openai-codex`
- 主模型是 `gpt-5.5`

## 阶段 3：处理 skills 边界

### 操作

明确不把以下业务用户 skills 当作开发 workflow 入口：

```text
~/.hermes/skills/domain/odoo-hedge*
```

它们可以作为业务语义参考，但不负责：

- issue workflow
- branch/worktree 管理
- CI 修复
- PR closeout
- Odoo test workflow

开发人员专用 skills 应另建，例如：

- `odoo-hedge-dev`
- `odoo-hedge-issue-workflow`
- `odoo-hedge-ci`
- `odoo-hedge-odoo-test`
- `odoo-hedge-pr-closeout`

### 验证

后续 `skills.external_dirs` 不应简单指向所有业务用户 domain skills 作为开发入口。

## 阶段 4：研究 `.codex` 接入方式

### 操作

优先阅读：

```text
/home/user/Repos/odoo-hedge/.codex/README.md
/home/user/Repos/odoo-hedge/.codex/config.toml
/home/user/Repos/odoo-hedge/.codex/rules/default.rules
```

盘点 `.codex/skills` 中哪些适合：

- 直接作为 Hermes `skills.external_dirs` 暴露
- 改写成 Hermes dev skill
- 改造成 local plugin 工具
- 暂不迁移

### 验证

输出一份 inventory 或 decision，列出 `.codex` skill 的处理策略。

## 阶段 5：验证 Codex 主力开发路径

### 操作

在 `odoo-hedge` profile 中，用一个只读任务验证：

```text
读取 /home/user/Repos/odoo-hedge/AGENTS.md，并总结开发约束。
```

再用一个轻量工具任务验证：

```text
列出最近 5 个 hedge_docs/issues 目录，并说明每个 issue dossier 是否有 README.md。
```

### 验证

预期：

- Hermes 从 `odoo-hedge` 根目录工作。
- Codex 主力模型能够正确读文件、搜索文件、执行只读命令。
- 不触发业务用户 domain skills 的误路由。

## 风险

- `hermes` CLI 当前不可见，可能需要修 PATH 或 install。
- Codex CLI 的 PATH 更新警告可能影响 `codex_app_server` runtime。
- 直接暴露 `.codex/skills` 可能引入与 Hermes skill 格式或工具命名不完全兼容的问题。
- 业务用户 skills 和开发人员 workflow 混用会造成任务路由混乱。

## 回滚

- 不改 Hermes core。
- profile 配置可恢复到默认 profile。
- `skills.external_dirs` 可清空。
- 新增开发 skills 可从 `~/.hermes/skills` 或 repo-local skill 目录移除。

## 后续计划

完成本 bootstrap 后，再推进：

1. `odoo-hedge` 开发人员专用 Hermes skills。
2. Slack gateway 接入。
3. dashboard `--tui` runbook。
4. DeepSeek 辅助 provider 验证。
5. local plugin 封装 `odoo-hedge` 常用开发命令。
