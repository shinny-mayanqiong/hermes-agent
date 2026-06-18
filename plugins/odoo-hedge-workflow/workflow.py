"""Dynamic DAG workflow logic for odoo-hedge development tasks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from hermes_cli import kanban_db as kb

WORKFLOW_TYPE = "odoo_hedge_dynamic_delivery_v1"
WORKFLOW_PLUGIN_AUTHOR = "odoo-hedge-workflow"

ORCHESTRATOR = "odoo-hedge-orchestrator"
ARCHITECT = "odoo-hedge-architect"
DESIGN_REVIEWER = "odoo-hedge-design-reviewer"
CODER = "odoo-hedge-coder"
SPEC_REVIEWER = "odoo-hedge-spec-reviewer"
QA = "odoo-hedge-qa"

PHASE_ASSIGNEE = {
    "architect_design": ARCHITECT,
    "architect_revise": ARCHITECT,
    "design_review": DESIGN_REVIEWER,
    "coder_implement": CODER,
    "coder_fix": CODER,
    "spec_review": SPEC_REVIEWER,
    "qa_verify": QA,
    "pr_ci": ORCHESTRATOR,
}

PHASE_SCHEMA = {
    "architect_design": "architect_design_v1",
    "architect_revise": "architect_design_v1",
    "design_review": "design_review_v1",
    "coder_implement": "coder_implement_v1",
    "coder_fix": "coder_implement_v1",
    "spec_review": "spec_review_v1",
    "qa_verify": "qa_verify_v1",
    "pr_ci": "pr_ci_v1",
}

PHASE_ALLOWED_ACTIONS = {
    "architect_design": ["read_repo", "inspect_issue", "write_design_artifact"],
    "architect_revise": ["read_repo", "inspect_review", "revise_design_artifact"],
    "design_review": ["read_design_artifact", "inspect_issue", "write_review_summary"],
    "coder_implement": ["edit_business_code", "edit_tests", "run_tests", "git_commit", "git_push"],
    "coder_fix": ["edit_business_code", "edit_tests", "run_tests", "git_commit", "git_push"],
    "spec_review": ["read_diff", "read_design_artifact", "write_review_summary"],
    "qa_verify": ["run_tests", "check_names", "check_translations", "write_qa_summary"],
    "pr_ci": ["create_pr", "watch_ci", "collect_review_comments"],
}

PHASE_FORBIDDEN_ACTIONS = {
    "architect_design": ["edit_business_code", "edit_tests", "git_commit", "git_push", "create_pr"],
    "architect_revise": ["edit_business_code", "edit_tests", "git_commit", "git_push", "create_pr"],
    "design_review": ["edit_business_code", "edit_tests", "git_commit", "git_push", "create_pr"],
    "spec_review": ["edit_business_code", "edit_tests", "git_commit", "git_push", "create_pr"],
    "qa_verify": ["edit_business_code", "git_commit", "git_push", "create_pr"],
}


@dataclass(frozen=True)
class WorkflowMeta:
    workflow_id: str
    root_task_id: str | None
    issue: int | None
    phase: str | None
    iteration: int
    repo: str
    worktree: str | None
    base_branch: str


def _json_block(data: dict[str, Any]) -> str:
    return "```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```"


def _extract_json_object(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    fenced = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _task_meta(task: kb.Task) -> dict[str, Any]:
    return _extract_json_object(task.body)


def _result_meta(task: kb.Task) -> dict[str, Any]:
    return _extract_json_object(task.result)


def _workflow_id(issue: int) -> str:
    return f"issue-{issue}"


def _root_body(*, issue: int, repo: str, worktree: str | None, base_branch: str) -> str:
    data = {
        "workflow_type": WORKFLOW_TYPE,
        "workflow_id": _workflow_id(issue),
        "entry": "existing_issue",
        "issue": issue,
        "repo": repo,
        "worktree": worktree,
        "base_branch": base_branch,
        "current_phase": "architect_design",
        "iteration": 1,
        "status": "running",
    }
    return "\n".join(
        [
            "Odoo Hedge dynamic delivery workflow root.",
            "",
            "该 task 是 workflow controller 状态容器，不直接执行开发。",
            "使用 `hermes -p odoo-hedge-orchestrator odoo-hedge-workflow tick <root_task_id>` 推进下一步。",
            "",
            _json_block(data),
        ]
    )


def _child_body(
    *,
    root: kb.Task,
    root_meta: dict[str, Any],
    phase: str,
    iteration: int,
    parent_task_id: str | None,
) -> str:
    assignee = PHASE_ASSIGNEE[phase]
    data = {
        "workflow_type": WORKFLOW_TYPE,
        "workflow_id": root_meta["workflow_id"],
        "root_task_id": root.id,
        "phase": phase,
        "iteration": iteration,
        "assignee_role": assignee.replace("odoo-hedge-", ""),
        "issue": root_meta.get("issue"),
        "repo": root_meta.get("repo"),
        "worktree": root_meta.get("worktree"),
        "base_branch": root_meta.get("base_branch"),
        "parent_task_id": parent_task_id,
        "allowed_actions": PHASE_ALLOWED_ACTIONS.get(phase, []),
        "forbidden_actions": PHASE_FORBIDDEN_ACTIONS.get(phase, []),
        "expected_output_schema": PHASE_SCHEMA[phase],
    }
    lines = [
        f"Odoo Hedge workflow child task: `{phase}` iteration {iteration}.",
        "",
        "请严格遵守 metadata 中的 allowed_actions / forbidden_actions。",
        "完成时必须在 task summary/result 中包含一个 JSON object，字段至少包括：",
        "",
        _json_block(
            {
                "workflow_id": root_meta["workflow_id"],
                "phase": phase,
                "iteration": iteration,
                "status": "done",
                "next_recommended_phase": "<phase>",
            }
        ),
        "",
        "Task metadata:",
        "",
        _json_block(data),
    ]
    return "\n".join(lines)


def _load_task(conn, task_id: str) -> kb.Task:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise ValueError(f"unknown task {task_id}")
    return kb.Task.from_row(row)


def _workflow_children(conn, root_task_id: str) -> list[kb.Task]:
    rows = conn.execute(
        """
        SELECT * FROM tasks
        WHERE body LIKE ?
          AND id != ?
          AND status != 'archived'
        ORDER BY created_at ASC, id ASC
        """,
        (f'%"root_task_id": "{root_task_id}"%', root_task_id),
    ).fetchall()
    return [kb.Task.from_row(row) for row in rows]


def _processed_child_ids(conn, root_task_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT body FROM task_comments WHERE task_id = ? ORDER BY created_at ASC",
        (root_task_id,),
    ).fetchall()
    processed: set[str] = set()
    for row in rows:
        body = row["body"] or ""
        processed.update(re.findall(r"processed_child_task:\s*(t_[a-zA-Z0-9]+)", body))
    return processed


def _validate_root(root: kb.Task) -> dict[str, Any]:
    meta = _task_meta(root)
    if meta.get("workflow_type") != WORKFLOW_TYPE:
        raise ValueError(f"task {root.id} is not an {WORKFLOW_TYPE} root task")
    if "workflow_id" not in meta:
        raise ValueError(f"task {root.id} is missing workflow_id")
    return meta


def _phase_title(phase: str, issue: int | None, iteration: int) -> str:
    issue_part = f"Issue #{issue}: " if issue else ""
    return f"{issue_part}{phase.replace('_', ' ')} v{iteration}"


def _create_child_task(
    conn,
    *,
    root: kb.Task,
    root_meta: dict[str, Any],
    phase: str,
    iteration: int,
    parent_task_id: str | None = None,
    board: str,
) -> str:
    if phase not in PHASE_ASSIGNEE:
        raise ValueError(f"unsupported phase {phase!r}")
    body = _child_body(
        root=root,
        root_meta=root_meta,
        phase=phase,
        iteration=iteration,
        parent_task_id=parent_task_id,
    )
    task_id = kb.create_task(
        conn,
        title=_phase_title(phase, root_meta.get("issue"), iteration),
        body=body,
        assignee=PHASE_ASSIGNEE[phase],
        created_by=WORKFLOW_PLUGIN_AUTHOR,
        workspace_kind="dir" if root_meta.get("worktree") else "dir",
        workspace_path=root_meta.get("worktree") or root_meta.get("repo"),
        priority=0,
        parents=[parent_task_id] if parent_task_id else [],
        idempotency_key=f"{root_meta['workflow_id']}:{root.id}:{phase}:{iteration}:{parent_task_id or 'initial'}",
        board=board,
    )
    return task_id


def start_workflow(
    *,
    issue: int,
    board: str,
    repo: str,
    worktree: str | None,
    base_branch: str,
    title: str | None = None,
) -> dict[str, Any]:
    root_title = title or f"Issue #{issue}: dynamic delivery workflow"
    with kb.connect_closing(board=board) as conn:
        root_id = kb.create_task(
            conn,
            title=root_title,
            body=_root_body(issue=issue, repo=repo, worktree=worktree, base_branch=base_branch),
            assignee=ORCHESTRATOR,
            created_by=WORKFLOW_PLUGIN_AUTHOR,
            workspace_kind="dir",
            workspace_path=worktree or repo,
            triage=True,
            idempotency_key=f"{WORKFLOW_TYPE}:{_workflow_id(issue)}:root",
            board=board,
        )
        root = _load_task(conn, root_id)
        root_meta = _validate_root(root)
        child_id = _create_child_task(
            conn,
            root=root,
            root_meta=root_meta,
            phase="architect_design",
            iteration=1,
            parent_task_id=None,
            board=board,
        )
        kb.add_comment(
            conn,
            root_id,
            WORKFLOW_PLUGIN_AUTHOR,
            "\n".join(
                [
                    "workflow_started: true",
                    f"created_child_task: {child_id}",
                    "next_phase: architect_design",
                ]
            ),
        )
    return {
        "kind": "start",
        "board": board,
        "root_task_id": root_id,
        "child_task_id": child_id,
        "child_phase": "architect_design",
    }


def _next_from_child(child: kb.Task) -> tuple[str | None, int, str]:
    meta = _task_meta(child)
    phase = meta.get("phase")
    iteration = int(meta.get("iteration") or 1)
    result = _result_meta(child)

    if phase in {"architect_design", "architect_revise"}:
        return "design_review", iteration, "architect design is ready for review"
    if phase == "design_review":
        if result.get("approved") is True:
            return "coder_implement", iteration, "design review approved"
        if result.get("approved") is False:
            return "architect_revise", iteration + 1, "design review requested changes"
        return None, iteration, "design_review result missing approved=true/false"
    if phase == "coder_implement":
        return "spec_review", iteration, "implementation is ready for spec review"
    if phase == "coder_fix":
        return "spec_review", iteration, "fix is ready for spec review"
    if phase == "spec_review":
        if result.get("pass") is True:
            return "qa_verify", iteration, "spec review passed"
        if result.get("pass") is False:
            return "coder_fix", iteration + 1, "spec review requested changes"
        return None, iteration, "spec_review result missing pass=true/false"
    if phase == "qa_verify":
        if result.get("pass") is True:
            return "pr_ci", iteration, "QA passed"
        if result.get("pass") is False:
            return "coder_fix", iteration + 1, "QA requested fixes"
        return None, iteration, "qa_verify result missing pass=true/false"
    if phase == "pr_ci":
        if result.get("success") is True:
            return None, iteration, "PR/CI succeeded; workflow awaits human review or closeout"
        if result.get("ci_failed") is True or result.get("success") is False:
            return "coder_fix", iteration + 1, "PR/CI requested fixes"
        return None, iteration, "pr_ci result missing success=true/false"
    return None, iteration, f"unsupported child phase {phase!r}"


def tick_workflow(*, root_task_id: str, board: str, apply: bool = False) -> dict[str, Any]:
    with kb.connect_closing(board=board) as conn:
        root = _load_task(conn, root_task_id)
        root_meta = _validate_root(root)
        children = _workflow_children(conn, root_task_id)
        processed = _processed_child_ids(conn, root_task_id)

        done_children = [
            child for child in children
            if child.status == "done" and child.id not in processed
        ]
        if not children:
            next_phase = "architect_design"
            next_iteration = 1
            reason = "workflow has no child tasks"
            processed_child = None
        elif not done_children:
            active = [c for c in children if c.status in {"ready", "todo", "running", "blocked", "review"}]
            return {
                "kind": "tick",
                "action": "wait",
                "reason": "no completed unprocessed child task",
                "active_children": [c.id for c in active],
            }
        else:
            done_children.sort(key=lambda c: (c.completed_at or 0, c.created_at, c.id))
            child = done_children[0]
            next_phase, next_iteration, reason = _next_from_child(child)
            processed_child = child

        if next_phase is None:
            if apply and processed_child is not None:
                kb.add_comment(
                    conn,
                    root_task_id,
                    WORKFLOW_PLUGIN_AUTHOR,
                    "\n".join(
                        [
                            f"processed_child_task: {processed_child.id}",
                            f"verdict: {reason}",
                            "created_child_task: none",
                        ]
                    ),
                )
            return {
                "kind": "tick",
                "action": "no_next_task",
                "reason": reason,
                "processed_child_task": processed_child.id if processed_child else None,
            }

        if not apply:
            return {
                "kind": "tick",
                "action": "create_next_task",
                "reason": reason,
                "processed_child_task": processed_child.id if processed_child else None,
                "next_phase": next_phase,
                "next_iteration": next_iteration,
            }

        created_id = _create_child_task(
            conn,
            root=root,
            root_meta=root_meta,
            phase=next_phase,
            iteration=next_iteration,
            parent_task_id=processed_child.id if processed_child else None,
            board=board,
        )
        kb.add_comment(
            conn,
            root_task_id,
            WORKFLOW_PLUGIN_AUTHOR,
            "\n".join(
                [
                    f"processed_child_task: {processed_child.id if processed_child else 'none'}",
                    f"verdict: {reason}",
                    f"created_child_task: {created_id}",
                    f"next_phase: {next_phase}",
                    f"next_iteration: {next_iteration}",
                ]
            ),
        )
        return {
            "kind": "tick",
            "action": "create_next_task",
            "reason": reason,
            "processed_child_task": processed_child.id if processed_child else None,
            "next_phase": next_phase,
            "next_iteration": next_iteration,
            "created_task_id": created_id,
        }


def workflow_status(root_task_id: str, *, board: str) -> dict[str, Any]:
    with kb.connect_closing(board=board) as conn:
        root = _load_task(conn, root_task_id)
        root_meta = _validate_root(root)
        children = _workflow_children(conn, root_task_id)
        processed = _processed_child_ids(conn, root_task_id)

    child_rows = []
    for child in children:
        meta = _task_meta(child)
        child_rows.append(
            {
                "id": child.id,
                "title": child.title,
                "status": child.status,
                "assignee": child.assignee,
                "phase": meta.get("phase"),
                "iteration": meta.get("iteration"),
                "processed": child.id in processed,
                "completed_at": child.completed_at,
            }
        )
    active = [c for c in child_rows if c["status"] in {"ready", "todo", "running", "blocked", "review"}]
    unprocessed_done = [c for c in child_rows if c["status"] == "done" and not c["processed"]]
    if unprocessed_done:
        next_action = f"run tick --plan/--apply to process {unprocessed_done[0]['id']}"
    elif active:
        next_action = f"wait for active child {active[0]['id']} ({active[0]['status']})"
    else:
        next_action = "no active child tasks"

    return {
        "kind": "status",
        "board": board,
        "root_task_id": root.id,
        "root_status": root.status,
        "workflow_id": root_meta.get("workflow_id"),
        "issue": root_meta.get("issue"),
        "current_phase": root_meta.get("current_phase"),
        "children": child_rows,
        "processed_child_tasks": sorted(processed),
        "next_action": next_action,
    }
