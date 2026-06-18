"""Dynamic DAG workflow logic for odoo-hedge development tasks."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
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


@dataclass(frozen=True)
class WorktreeResolution:
    repo: str
    worktree: str
    branch: str | None
    topic: str | None
    topic_source: str | None
    source: str
    resources: dict[str, Any]


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


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:64]


def _valid_topic(value: str | None) -> str | None:
    topic = (value or "").strip()
    if not topic:
        return None
    if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", topic):
        return topic
    return None


def _issue_slug(issue: int, topic: str | None) -> str:
    topic = _valid_topic(topic) or _slugify(topic or "")
    return f"issue-{issue}-{topic}" if topic else f"issue-{issue}"


def _default_worktree_root(repo: Path) -> Path:
    override = os.environ.get("WORKTREE_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (repo.parent / "odoo-hedge-worktrees").resolve()


def _branch_short(ref: str | None) -> str | None:
    if not ref:
        return None
    return ref.removeprefix("refs/heads/")


def _parse_worktree_list(output: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        entries.append(current)
    return entries


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _matching_issue_worktree(repo: Path, issue: int) -> tuple[str, str | None] | None:
    proc = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        text=True,
        capture_output=True,
        check=True,
    )
    repo_real = repo.resolve()
    worktree_root = _default_worktree_root(repo_real)
    issue_re = re.compile(rf"(^|[/-])issue-{issue}($|[-_/])")
    candidates: list[tuple[int, str, str]] = []

    for entry in _parse_worktree_list(proc.stdout):
        raw_path = entry.get("worktree")
        if not raw_path:
            continue
        path = Path(raw_path).expanduser().resolve()
        if path == repo_real:
            continue
        branch = _branch_short(entry.get("branch"))
        haystacks = [branch or "", path.name]
        if not any(issue_re.search(value) for value in haystacks):
            continue

        under_default_root = _path_is_relative_to(path, worktree_root)
        branch_priority = 2
        if branch == f"codex/issue-{issue}":
            branch_priority = 0
        elif branch and branch.startswith(f"codex/issue-{issue}-"):
            branch_priority = 1
        priority = (0 if under_default_root else 10) + branch_priority
        candidates.append((priority, str(path), branch or ""))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    _, path, branch = candidates[0]
    return path, branch or None


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def _read_conf_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _worktree_resources(worktree: Path) -> dict[str, Any]:
    env = _read_env_file(worktree / ".env")
    config = env.get("ODOO_CONFIG") or "odoo.local.conf"
    config_path = Path(config)
    if not config_path.is_absolute():
        config_path = worktree / config_path
    conf = _read_conf_file(config_path)
    db_name = env.get("ODOO_DB_NAME")
    return {
        "odoo_db_name": db_name,
        "odoo_test_db_name": f"{db_name}_test" if db_name else None,
        "python_venv_path": env.get("PYTHON_VENV_PATH"),
        "odoo_config": config,
        "http_port": conf.get("http_port"),
        "gevent_port": conf.get("gevent_port"),
    }


def _github_repo_slug(repo: Path) -> str | None:
    try:
        remote = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        return None

    match = re.search(r"github\.com[:/]([^/]+)/([^/.]+)(?:\.git)?$", remote)
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def _topic_from_issue_body(repo: Path, issue: int) -> tuple[str | None, str | None]:
    repo_slug = _github_repo_slug(repo) or "shinnytech/odoo-hedge"
    try:
        body = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                str(issue),
                "--repo",
                repo_slug,
                "--json",
                "body",
                "--jq",
                ".body",
            ],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None, None

    match = re.search(
        r"(?im)^##\s+Topic\s*$\s*([^\n#][^\n]*)",
        body,
    )
    if not match:
        return None, None
    topic = _valid_topic(match.group(1))
    if not topic:
        return None, None
    return topic, "issue_body"


def _resolve_topic(repo: Path, issue: int, topic: str | None) -> tuple[str | None, str | None]:
    explicit = _valid_topic(topic)
    if explicit:
        return explicit, "explicit"
    if topic and topic.strip():
        return _slugify(topic), "explicit_slugified"
    return _topic_from_issue_body(repo, issue)


def _ensure_existing_worktree(path: str) -> Path:
    worktree = Path(path).expanduser().resolve()
    if not worktree.exists():
        raise ValueError(f"worktree path does not exist: {worktree}")
    subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--git-dir"],
        text=True,
        capture_output=True,
        check=True,
    )
    return worktree


def _branch_for_issue(issue: int, branch: str | None, topic: str | None) -> str:
    if branch and branch.strip():
        return branch.strip()
    return f"codex/{_issue_slug(issue, topic)}"


def _create_worktree(repo: Path, issue: int, base_branch: str, branch: str | None, topic: str | None) -> tuple[Path, str]:
    final_branch = _branch_for_issue(issue, branch, topic)
    script = repo / "scripts" / "codex-worktree.sh"
    if not script.exists():
        raise ValueError(f"worktree bootstrap script not found: {script}")
    subprocess.run(
        [str(script), "create", final_branch, base_branch, "--no-codex"],
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=True,
    )
    safe_branch = final_branch.replace("/", "-")
    worktree = _default_worktree_root(repo) / safe_branch
    if not worktree.exists():
        raise ValueError(f"worktree script completed but path was not created: {worktree}")
    return worktree.resolve(), final_branch


def _resolve_worktree(
    *,
    issue: int,
    repo: str,
    worktree: str | None,
    base_branch: str,
    branch: str | None,
    topic: str | None,
    create_worktree: bool,
) -> WorktreeResolution:
    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.exists():
        raise ValueError(f"repo path does not exist: {repo_path}")

    if worktree:
        worktree_path = _ensure_existing_worktree(worktree)
        topic_value, topic_source = _resolve_topic(repo_path, issue, topic)
        try:
            branch_name = subprocess.run(
                ["git", "-C", str(worktree_path), "rev-parse", "--abbrev-ref", "HEAD"],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
        except subprocess.CalledProcessError:
            branch_name = branch
        return WorktreeResolution(
            repo=str(repo_path),
            worktree=str(worktree_path),
            branch=branch_name or branch,
            topic=topic_value,
            topic_source=topic_source,
            source="explicit",
            resources=_worktree_resources(worktree_path),
        )

    topic_value, topic_source = _resolve_topic(repo_path, issue, topic)
    existing = _matching_issue_worktree(repo_path, issue)
    if existing:
        path, branch_name = existing
        worktree_path = Path(path).resolve()
        return WorktreeResolution(
            repo=str(repo_path),
            worktree=str(worktree_path),
            branch=branch_name,
            topic=topic_value,
            topic_source=topic_source,
            source="existing",
            resources=_worktree_resources(worktree_path),
        )

    if not create_worktree:
        raise ValueError(
            f"no existing worktree found for issue {issue}; rerun without --no-create-worktree "
            "or pass --worktree explicitly"
        )

    worktree_path, branch_name = _create_worktree(repo_path, issue, base_branch, branch, topic_value)
    return WorktreeResolution(
        repo=str(repo_path),
        worktree=str(worktree_path),
        branch=branch_name,
        topic=topic_value,
        topic_source=topic_source,
        source="created",
        resources=_worktree_resources(worktree_path),
    )


def _root_body(
    *,
    issue: int,
    repo: str,
    worktree: str,
    base_branch: str,
    branch: str | None,
    topic: str | None,
    topic_source: str | None,
    worktree_source: str,
    worktree_resources: dict[str, Any],
) -> str:
    data = {
        "workflow_type": WORKFLOW_TYPE,
        "workflow_id": _workflow_id(issue),
        "entry": "existing_issue",
        "issue": issue,
        "repo": repo,
        "worktree": worktree,
        "branch": branch,
        "topic": topic,
        "topic_source": topic_source,
        "base_branch": base_branch,
        "worktree_source": worktree_source,
        "worktree_resources": worktree_resources,
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
        "branch": root_meta.get("branch"),
        "topic": root_meta.get("topic"),
        "base_branch": root_meta.get("base_branch"),
        "worktree_resources": root_meta.get("worktree_resources") or {},
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
        workspace_kind="worktree",
        workspace_path=root_meta.get("worktree"),
        branch_name=root_meta.get("branch"),
        priority=0,
        parents=[parent_task_id] if parent_task_id else [],
        idempotency_key=f"{root_meta['workflow_id']}:{root.id}:{phase}:{iteration}:{parent_task_id or 'initial'}",
        board=board,
    )
    _refresh_task_workspace(
        conn,
        task_id,
        body=body,
        workspace_path=root_meta.get("worktree"),
        branch_name=root_meta.get("branch"),
    )
    return task_id


def _refresh_task_workspace(
    conn,
    task_id: str,
    *,
    body: str,
    workspace_path: str | None,
    branch_name: str | None,
    status: str | None = None,
) -> None:
    status_sql = ", status = ?" if status else ""
    params: list[Any] = [body, workspace_path, branch_name]
    if status:
        params.append(status)
    params.append(task_id)
    with kb.write_txn(conn):
        conn.execute(
            f"""
            UPDATE tasks
               SET body = ?,
                   workspace_kind = 'worktree',
                   workspace_path = ?,
                   branch_name = ?
                   {status_sql}
             WHERE id = ?
            """,
            tuple(params),
        )
        conn.execute(
            "INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, 'edited', ?, strftime('%s','now'))",
            (
                task_id,
                json.dumps(
                    {
                        "source": WORKFLOW_PLUGIN_AUTHOR,
                        "reason": "refresh workflow worktree metadata",
                        "workspace_path": workspace_path,
                        "branch_name": branch_name,
                    },
                    ensure_ascii=False,
                ),
            ),
        )


def start_workflow(
    *,
    issue: int,
    board: str,
    repo: str,
    worktree: str | None,
    base_branch: str,
    branch: str | None = None,
    topic: str | None = None,
    create_worktree: bool = True,
    title: str | None = None,
) -> dict[str, Any]:
    resolved = _resolve_worktree(
        issue=issue,
        repo=repo,
        worktree=worktree,
        base_branch=base_branch,
        branch=branch,
        topic=topic,
        create_worktree=create_worktree,
    )
    root_title = title or f"Issue #{issue}: dynamic delivery workflow"
    root_body = _root_body(
        issue=issue,
        repo=resolved.repo,
        worktree=resolved.worktree,
        base_branch=base_branch,
        branch=resolved.branch,
        topic=resolved.topic,
        topic_source=resolved.topic_source,
        worktree_source=resolved.source,
        worktree_resources=resolved.resources,
    )
    with kb.connect_closing(board=board) as conn:
        root_id = kb.create_task(
            conn,
            title=root_title,
            body=root_body,
            assignee=ORCHESTRATOR,
            created_by=WORKFLOW_PLUGIN_AUTHOR,
            workspace_kind="worktree",
            workspace_path=resolved.worktree,
            branch_name=resolved.branch,
            initial_status="blocked",
            idempotency_key=f"{WORKFLOW_TYPE}:{_workflow_id(issue)}:root",
            board=board,
        )
        _refresh_task_workspace(
            conn,
            root_id,
            body=root_body,
            workspace_path=resolved.worktree,
            branch_name=resolved.branch,
            status="blocked",
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
                    f"worktree_source: {resolved.source}",
                    f"worktree: {resolved.worktree}",
                    f"branch: {resolved.branch or ''}",
                    f"topic: {resolved.topic or ''}",
                ]
            ),
        )
    return {
        "kind": "start",
        "board": board,
        "root_task_id": root_id,
        "child_task_id": child_id,
        "child_phase": "architect_design",
        "branch": resolved.branch,
        "topic": resolved.topic,
        "topic_source": resolved.topic_source,
        "worktree": resolved.worktree,
        "worktree_source": resolved.source,
        "worktree_resources": resolved.resources,
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
