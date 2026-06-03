# 2026-06-03 迁移 Codex 开发流程到 Hermes

## 目标

把 `/home/user/Repos/odoo-hedge/.codex` 中成熟的开发 workflow，逐步迁移到
Hermes `odoo-hedge-dev` profile，让 Hermes 可以作为统一开发助手入口。

本迁移不是替换 Codex。当前开发主力仍是 Codex。

Hermes 的目标角色：

- 统一 profile 和工作目录
- 统一 CLI/TUI、Slack、web dashboard 入口
- 承载开发人员专用 skills
- 记录 session、logs、runbooks、decisions
- 逐步把高频流程做成 local plugin 或 hooks

## 背景

用户确认：

- `~/.hermes/skills/domain/odoo-hedge*` 面向业务用户，不作为开发人员 workflow。
- `odoo-hedge/.cursor/skills` 暂时忽略。
- 当前开发主力是 Codex。

已完成：

- Hermes 本地文档目录已创建。
- `odoo-hedge-dev` profile 已创建。
- `openai-codex` 过期 token 已重新登录成功。

仍需完成：

- `odoo-hedge-dev` profile 的 `terminal.cwd` 指向 `/home/user/Repos/odoo-hedge`。
- 明确 `.codex/skills` 哪些直接接入，哪些改写，哪些暂缓。
- 创建开发人员专用 Hermes skills。

## 不做什么

第一阶段不做：

- 不迁移 `.cursor/skills`。
- 不把业务用户 domain skills 当作开发入口。
- 不改 Hermes core。
- 不自动执行 DB reset。
- 不启用 Slack gateway 自动化。
- 不启用 scheduled weekly/daily publish。

## 阶段 1：完成 profile 基础验证

### 步骤

1. 修改 `odoo-hedge-dev` profile：

```yaml
terminal:
  cwd: /home/user/Repos/odoo-hedge
```

2. 启动：

```bash
cd /home/user/Repos/hermes-agent
source .venv/bin/activate
hermes -p odoo-hedge-dev
```

3. 在 Hermes 中验证：

```text
请运行 pwd，并告诉我当前工作目录。
```

### 验证

预期当前目录：

```text
/home/user/Repos/odoo-hedge
```

## 阶段 2：建立开发总入口 skill

### 目标 skill

```text
odoo-hedge-dev
```

### 作用

该 skill 是开发人员 workflow 总入口，不负责业务用户问答。

它应明确：

- 必须先读 `/home/user/Repos/odoo-hedge/AGENTS.md`。
- 必须再读 `/home/user/Repos/odoo-hedge/hedge_docs/README.md`。
- `master` 是保护分支，不能直接开发。
- 普通业务开发任务仍以 GitHub Issue / Project 8 / `hedge_docs` 为事实源。
- 只把 Hermes 文档目录用于“开发助手系统本身”的改进。
- Codex 是主力开发模型。

### 输入来源

- `/home/user/Repos/odoo-hedge/AGENTS.md`
- `/home/user/Repos/odoo-hedge/hedge_docs/README.md`
- `/home/user/Repos/odoo-hedge/.codex/config.toml`
- `/home/user/Repos/odoo-hedge/.codex/rules/default.rules`

### 验证

让 Hermes 回答：

```text
你现在作为 odoo-hedge 开发助手，开始任何 repo-specific 工作前要读哪些文件？
```

预期回答应包含：

- `AGENTS.md`
- `hedge_docs/README.md`
- 不在 `master` 上直接实现
- 实际任务状态以 GitHub Issue / Project 8 为准

## 阶段 3：迁移第一批开发 workflow skills

第一批目标：

1. `odoo-hedge-worktree`
2. `odoo-hedge-ci`
3. `odoo-hedge-issue-workflow`
4. `odoo-hedge-i18n`

### `odoo-hedge-worktree`

来源：

- `.codex/skills/issue-worktree-bootstrap/SKILL.md`

目标：

- 统一 issue/phase worktree 创建流程。
- 保留 `scripts/codex-worktree.sh` 作为事实工具。
- 明确 worktree、DB、ports、`.env`、`odoo.local.conf` 输出要求。

### `odoo-hedge-ci`

来源：

- `.codex/skills/hedge-ci-watch-repair-loop/SKILL.md`
- `scripts/ci_check.py`

目标：

- 本地 CI gate 入口。
- PR CI watch 和 repair loop 入口。
- 区分本地验证和 GitHub Actions repair。

### `odoo-hedge-issue-workflow`

来源：

- `.codex/skills/hedge-issue-delivery-loop/SKILL.md`
- `.codex/skills/gh-pr-review-followup/SKILL.md`
- `.codex/skills/issue-closeout-sync/SKILL.md`

目标：

- 从 issue 到 branch/worktree、实现、验证、PR、CI、closeout 的完整开发 workflow。
- 保持实际任务状态在 `odoo-hedge` 项目体系内。

### `odoo-hedge-i18n`

来源：

- `.codex/skills/translate-hedge-zh-cn/SKILL.md`

目标：

- 迁移翻译 workflow。
- 避免使用旧 `.cursor/skills`。
- 保留 `.env` 中 `PYTHON_VENV_PATH` 的约束，不写死 `.venv`。

## 阶段 4：决定 skills 放置位置

候选方案：

### 方案 A：放到 Hermes 用户 skills

路径：

```text
~/.hermes/skills/dev/odoo-hedge-*
```

优点：

- Hermes 立即可用。
- 不影响 `odoo-hedge` repo。

缺点：

- 团队共享弱。
- 需要另行备份和同步。

### 方案 B：放到 `odoo-hedge/.codex/skills` 并通过 `skills.external_dirs` 接入

优点：

- 继续复用 Codex 既有资产。
- 跟随 `odoo-hedge` repo 共享。

缺点：

- Hermes 与 Codex skill 语义不完全一致。
- 需要避免把不适合 Hermes 的 skill 全量暴露。

### 方案 C：在 `odoo-hedge` 新建 Hermes-specific skills 目录

示例：

```text
/home/user/Repos/odoo-hedge/.hermes/skills/
```

优点：

- 与 Codex skills 分离。
- 可作为团队共享资产。
- 便于通过 `skills.external_dirs` 接入。

缺点：

- 需要确认 `odoo-hedge` repo 是否允许新增该目录。
- 需要维护 `.gitignore` / repo policy。

## 初步建议

短期：

- 先使用方案 A 或 C 做少量开发 skills 原型。
- 不直接把整个 `.codex/skills` 目录加入 `skills.external_dirs`。

中期：

- 把稳定的 Hermes dev skills 放入 `odoo-hedge` repo 的共享目录。
- 用 `skills.external_dirs` 精确指向该目录。

长期：

- 高频、参数化、危险或需要结构化输出的流程，转成 Hermes local plugin 工具。

## 阶段 5：工具化候选

第一批只记录，不实现：

- `hedge_ci_check`
- `hedge_worktree_create`
- `hedge_pr_context`
- `hedge_project8_status`
- `hedge_db_reset`

原则：

- destructive 操作必须确认。
- DB reset 不做第一批自动化。
- GitHub / Project 8 写操作先走 confirmation-first workflow。

## 验收标准

第一轮迁移完成时，应满足：

- `odoo-hedge-dev` profile 从 `/home/user/Repos/odoo-hedge` 工作。
- 至少一个开发总入口 skill 可用。
- 至少一个高频 workflow skill 可用，例如 `odoo-hedge-ci`。
- 不误用业务用户 domain skills。
- 不依赖 `.cursor/skills`。
- 不改 Hermes core。

## 下一步

1. 先完成 `terminal.cwd` 验证。
2. 决定 dev skills 放置位置：用户目录、`odoo-hedge` repo 内目录，或先两者并行。
3. 创建第一版 `odoo-hedge-dev` Hermes skill。
4. 从 `odoo-hedge-ci` 开始迁移第一个具体 workflow。
