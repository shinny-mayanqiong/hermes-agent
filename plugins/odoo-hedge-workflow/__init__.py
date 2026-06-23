"""Odoo Hedge dynamic DAG workflow plugin."""

from __future__ import annotations

from .cli import odoo_hedge_workflow_command, register_cli
from .pr_review import pr_review_command, pre_gateway_dispatch
from .workflow import kanban_spawn_override


def register(ctx) -> None:
    ctx.register_cli_command(
        name="odoo-hedge-workflow",
        help="Operate Odoo Hedge dynamic DAG workflows",
        setup_fn=register_cli,
        handler_fn=odoo_hedge_workflow_command,
        description=(
            "Project-specific workflow controller for odoo-hedge delivery. "
            "Creates root tasks, advances dynamic DAG phases, and reports status."
        ),
    )
    ctx.register_command(
        "pr-review",
        pr_review_command,
        description="Review an Odoo Hedge PR via Codex.",
        args_hint="<PR_URL|PR_NUMBER> [focus]",
        platforms=("slack",),
    )
    ctx.register_command(
        "odoo-hedge-pr-review",
        pr_review_command,
        description="Review an Odoo Hedge PR via Codex.",
        args_hint="<PR_URL|PR_NUMBER> [focus]",
        platforms=("slack",),
    )
    ctx.register_hook("kanban_spawn_override", kanban_spawn_override)
    ctx.register_hook("pre_gateway_dispatch", pre_gateway_dispatch)
