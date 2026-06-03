# Inventory

本目录记录已经发现的本地资产、配置、脚本、skills、plugins、缺口和迁移候选。

Inventory 是事实盘点，不是计划，也不是最终决策。它应该尽量写“现在有什么”和
“在哪里”，少写主观设计结论。

## 适合放在这里的内容

- 现有 `~/.hermes/skills/domain/odoo-hedge*` skills 盘点
- `odoo-hedge/.cursor/skills` 可迁移内容盘点
- `odoo-hedge/.codex` 资产盘点
- Hermes 当前 `config.yaml` 相关设置摘要
- Hermes 当前 `.env` 变量名盘点，不写值
- gateway、dashboard、API Server 依赖与状态盘点
- 可复用 scripts 和缺失工具清单

## 不适合放在这里的内容

- 密钥值、token、password
- 业务需求优先级
- 具体 issue 的实现计划
- 未确认的运行结果

## 文档格式建议

每个 inventory 文档建议包含：

- 盘点日期
- 范围
- 发现项
- 可复用项
- 缺口
- 风险
- 建议后续动作

文件名建议使用：

```text
YYYY-MM-DD-short-topic.md
```

例如：

```text
2026-06-03-existing-hedge-skills.md
```

## 初始盘点候选

- 本机现有 Hermes `odoo-hedge` domain skills
- `odoo-hedge` repo 中的 Codex/Cursor 资产
- Hermes provider 配置与 Codex/DeepSeek 可用性
- Slack / dashboard / API Server 当前配置状态
