"""Dynamic DAG workflow logic for odoo-hedge development tasks."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hermes_cli import kanban_db as kb

WORKFLOW_TYPE = "odoo_hedge_dynamic_delivery_v1"
WORKFLOW_PLUGIN_AUTHOR = "odoo-hedge-workflow"
AUTO_DEFAULT_POLL_SECONDS = 30.0
AUTO_DEFAULT_MAX_STEPS = 1000
PR_REVIEW_DEFAULT_REPO = "/home/user/Repos/odoo-hedge"
PR_REVIEW_DEFAULT_BOARD = "odoo-hedge-dev"
PR_REVIEW_DEFAULT_REPO_SLUG = "shinnytech/odoo-hedge"
PR_REVIEW_ENTRY = "pr_review"

ORCHESTRATOR = "odoo-hedge-orchestrator"
SPEC = "odoo-hedge-spec"
SPEC_REVIEWER = "odoo-hedge-spec-reviewer"
CODER = "odoo-hedge-coder"
CI = "odoo-hedge-ci"
CODE_REVIEWER = "odoo-hedge-code-reviewer"
PR_REVIEWER = "odoo-hedge-pr-reviewer"
CLOSEOUT = "odoo-hedge-closeout"
I18N = "odoo-hedge-i18n"
PROJECT = "odoo-hedge-project"

PHASE_ASSIGNEE = {
    "spec_discussion": SPEC,
    "spec_freeze": SPEC,
    "blueprint_prompts": SPEC,
    "spec_blueprint_review": SPEC_REVIEWER,
    "implementation": CODER,
    "ci_watch_repair": CI,
    "branch_sync_repair": CI,
    "local_code_review": CODE_REVIEWER,
    "pr_review_followup": PR_REVIEWER,
    "closeout_sync": CLOSEOUT,
    "i18n_check": I18N,
    "project_followup": PROJECT,
}

PHASE_SCHEMA = {
    "spec_discussion": "spec_discussion_v2",
    "spec_freeze": "spec_freeze_v2",
    "blueprint_prompts": "blueprint_prompts_v2",
    "spec_blueprint_review": "spec_blueprint_review_v2",
    "implementation": "implementation_v2",
    "ci_watch_repair": "ci_watch_repair_v2",
    "branch_sync_repair": "branch_sync_repair_v2",
    "local_code_review": "local_code_review_v2",
    "pr_review_followup": "pr_review_followup_v2",
    "closeout_sync": "closeout_sync_v2",
    "i18n_check": "i18n_check_v2",
    "project_followup": "project_followup_v2",
}

PHASE_OUTPUT_EXTRAS = {
    "implementation": {
        "artifacts": [],
        "blockers": [],
        "needs_user_input": False,
        "needs_followup_issue": False,
        "requires_i18n": False,
    },
    "ci_watch_repair": {
        "success": True,
        "ci_failed": False,
        "mergeable": "MERGEABLE",
        "merge_state_status": "CLEAN",
        "artifacts": [],
        "blockers": [],
    },
    "branch_sync_repair": {
        "success": True,
        "synced": True,
        "conflicts_resolved": True,
        "retry_branch_sync": False,
        "mergeable": "MERGEABLE",
        "merge_state_status": "CLEAN",
        "needs_user_input": False,
        "artifacts": [],
        "blockers": [],
    },
    "spec_blueprint_review": {
        "approved": True,
        "review_decision": "approved|changes_requested",
        "return_phase": "blueprint_prompts",
        "review_summary": "",
        "ui_ux_impact": "",
        "clarity_findings": [],
        "step_findings": [],
        "blocking_findings": [],
        "non_blocking_findings": [],
    },
    "local_code_review": {
        "approved": True,
        "review_decision": "approved|changes_requested",
        "review_summary": "",
        "ui_ux_impact": "",
        "blocking_findings": [],
        "non_blocking_findings": [],
        "pr_comment_url": "",
        "artifacts": [],
        "requires_i18n": False,
        "needs_followup_issue": False,
    },
    "pr_review_followup": {
        "comments_resolved": True,
        "unresolved_comments": [],
        "artifacts": [],
    },
    "closeout_sync": {
        "closeout_synced": True,
        "pr_merged": False,
        "issue_closed": False,
        "project_status": "In Review|Done",
        "closeout_summary": "",
        "artifacts": [],
        "blockers": [],
    },
    "i18n_check": {
        "success": True,
        "artifacts": [],
        "blockers": [],
    },
    "project_followup": {
        "created_issues": [],
        "return_phase": "implementation",
        "blockers": [],
    },
}

PHASE_SKILL = {
    "spec_discussion": "brainstorm-spec",
    "spec_freeze": "spec-freeze",
    "blueprint_prompts": "spec-blueprint-prompts",
    "implementation": "hedge-issue-delivery-loop",
    "ci_watch_repair": "hedge-ci-watch-repair-loop",
    "branch_sync_repair": "hedge-ci-watch-repair-loop",
    "pr_review_followup": "gh-pr-review-followup",
    "closeout_sync": "issue-closeout-sync",
    "i18n_check": "translate-hedge-zh-cn",
    "project_followup": "gh-project-task-status",
}

PHASE_EXECUTION_BACKEND = {
    "implementation": "codex_exec",
    "ci_watch_repair": "codex_exec",
    "branch_sync_repair": "codex_exec",
    "local_code_review": "codex_exec",
    "pr_review_followup": "codex_exec",
    "closeout_sync": "codex_exec",
}

PHASE_ALLOWED_ACTIONS = {
    "spec_discussion": ["inspect_issue", "ask_user_questions", "write_discussion_artifact"],
    "spec_freeze": ["read_discussion", "write_frozen_spec"],
    "blueprint_prompts": ["read_spec", "write_blueprint_artifacts"],
    "spec_blueprint_review": ["read_spec", "read_blueprint", "write_review_summary"],
    "implementation": ["edit_business_code", "edit_tests", "run_tests", "git_commit", "git_push", "create_pr"],
    "ci_watch_repair": ["watch_ci", "edit_tests", "edit_business_code", "git_commit", "git_push"],
    "branch_sync_repair": ["fetch_base_branch", "rebase_or_merge_base", "resolve_conflicts", "run_tests", "git_commit", "git_push"],
    "local_code_review": ["read_diff", "read_spec", "read_pr", "write_review_summary", "post_pr_comment"],
    "pr_review_followup": ["read_pr_comments", "edit_business_code", "edit_tests", "git_commit", "git_push"],
    "closeout_sync": ["read_pr", "read_issue", "sync_closeout_artifacts"],
    "i18n_check": ["edit_translations", "run_i18n_checks", "git_commit"],
    "project_followup": ["create_followup_issue", "update_project_status"],
}

PHASE_FORBIDDEN_ACTIONS = {
    "spec_discussion": ["edit_business_code", "git_commit", "git_push", "create_pr"],
    "spec_freeze": ["edit_business_code", "git_commit", "git_push", "create_pr"],
    "blueprint_prompts": ["edit_business_code", "git_commit", "git_push", "create_pr"],
    "spec_blueprint_review": ["edit_business_code", "git_commit", "git_push", "create_pr"],
    "local_code_review": ["edit_business_code", "git_commit", "git_push", "create_pr"],
    "closeout_sync": [
        "edit_business_code",
        "merge_pr",
        "close_pr",
        "reopen_pr",
        "close_issue_before_pr_merged",
        "move_project_done_before_pr_merged",
    ],
    "project_followup": ["edit_business_code", "git_commit", "git_push"],
}

PHASE_INSTRUCTIONS = {
    "spec_blueprint_review": [
        "本阶段是开发前文档 review，只审查 spec 和 blueprint 是否足以指导实现，不修改业务代码。",
        "必须判断 spec 是否清楚描述了要解决的问题、目标用户/业务场景、范围、非范围、验收标准和风险边界。",
        "必须判断 spec 或 blueprint 是否说明 UI/UX impact：涉及哪些页面、菜单、按钮、表单字段、状态展示、提示文案或用户操作流；如果没有可见变化，也必须明确写出无可见 UI/UX 变化。",
        "必须判断 blueprint 的阶段划分、执行顺序、验证方式和回滚/异常处理是否合理，是否能支撑后续 implementation 阶段直接执行。",
        "如果发现需求定义不清、步骤缺失、验收不可执行、实现顺序风险高或与 odoo-hedge repo 约束冲突，设置 approved=false，并在 blocking_findings 中给出必须修正的问题。",
        "如果存在用户可见变化但 UI/UX impact 未说明或无法验证，设置 approved=false，除非能明确证明该遗漏不影响实现和验收。",
        "如果只有不影响开发启动的优化建议，可以设置 approved=true，并把建议放入 non_blocking_findings。",
        "review 结论必须包含 review_summary、ui_ux_impact、clarity_findings、step_findings、blocking_findings、non_blocking_findings、approved 和 review_decision。",
        "approved=false 时必须设置 return_phase；如果是需求/spec 问题，return_phase=spec_freeze；如果是执行工件/步骤问题，return_phase=blueprint_prompts。",
    ],
    "branch_sync_repair": [
        "本阶段目标是修复 PR branch 与 base branch 的可合并状态，不是普通 CI 观察。",
        "必须先读取 PR mergeable / mergeStateStatus；如果是 CONFLICTING、DIRTY、UNKNOWN 或 behind，必须 fetch base branch 并 rebase 或 merge base branch。",
        "如果发生冲突，优先解决 branch sync 冲突并保持原 feature scope；无法判断业务取舍时设置 needs_user_input=true。",
        "完成后必须 push 当前 branch，并重新读取 PR mergeable / mergeStateStatus。",
        "最终 JSON 必须包含 success、synced、conflicts_resolved、mergeable、merge_state_status。",
        "如果未实际完成 branch sync，但仍需要重试本阶段，设置 success=false 和 retry_branch_sync=true。",
    ],
    "local_code_review": [
        "本阶段是开发完成且 CI 通过后的本地 code review，不修改业务代码、不提交、不 push、不创建 PR。",
        "必须先读取 PR、issue/spec、blueprint、CI 结果和当前 branch diff，明确这个 PR 解决了什么问题，以及通过哪些代码和测试改动解决。",
        "必须分析需求本身是否合理：是否符合 issue/spec，是否边界清楚，是否存在应拆 follow-up issue 的范围扩张。",
        "必须明确分析 UI/UX impact：哪些用户页面、菜单、按钮、表单字段、列表可见性、状态展示、提示文案或用户操作流发生变化；如果没有可见变化，也必须明确写出无可见 UI/UX 变化。",
        "必须分析实现方案是否合理：业务逻辑、Odoo 模型/API 使用、权限/状态机、错误处理、事务/幂等、测试覆盖、翻译和命名是否与 odoo-hedge 约束一致。",
        "必须给出明确 review 结论：approved=true/false 和 review_decision。存在会影响正确性、数据安全、可维护性或验收的阻塞问题时，approved=false。",
        "必须把详细 review 结论和改进建议直接发送为 PR comment；comment 应包含问题背景、实现概述、UI/UX impact、review 结论、blocking findings、non-blocking findings 和建议的后续动作。",
        "如果 PR body 或已有 comment 没有说明 UI/UX impact，本阶段必须在 PR comment 中补齐；如果用户可见变化缺少验证证据，应作为 blocking 或 non-blocking finding 明确列出。",
        "最终 JSON 必须包含 review_summary、ui_ux_impact、blocking_findings、non_blocking_findings、approved、review_decision 和 pr_comment_url。",
        "如果发现需要新增/修改可见 UI 文案，设置 requires_i18n=true；如果需要拆 follow-up issue，设置 needs_followup_issue=true。",
    ],
    "closeout_sync": [
        "本阶段只做 PR / issue / Project / hedge_docs 的 closeout evidence 和状态同步，不负责实现、修复、merge PR、close PR 或 reopen PR。",
        "必须先读取 PR 当前事实：state、merged、mergedAt、base/head、CI/checks、review 状态，以及相关 issue 和 Project 8 状态。",
        "禁止 merge PR；禁止 close PR；禁止 reopen PR。PR 是否合并只按 GitHub 当前事实记录到 pr_merged。",
        "如果 PR 尚未 merge，不得 close issue，不得把 Project 8 移动到 Done；应保持或设置为 In Review，并在 blockers 中说明缺少 merge 事实。",
        "如果 PR 已 merge，且验收命令、风险/回滚说明和 closeout 证据完整，可以按 issue-closeout-sync skill 同步 issue closeout、关闭 issue，并把 Project 8 移动到 Done。",
        "如果验收证据不完整，即使 PR 已 merge，也不得强行标记 Done；应记录 blockers 和下一步人工动作。",
        "最终 JSON 必须包含 closeout_synced、pr_merged、issue_closed、project_status、closeout_summary、artifacts、blockers。",
    ],
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


@dataclass(frozen=True)
class PullRequestRef:
    owner: str
    repo_name: str
    number: int
    url: str

    @property
    def repo_slug(self) -> str:
        return f"{self.owner}/{self.repo_name}"


def _json_block(data: dict[str, Any]) -> str:
    return "```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```"


def _phase_output_example(root_meta: dict[str, Any], phase: str, iteration: int) -> dict[str, Any]:
    data = {
        "workflow_id": root_meta["workflow_id"],
        "phase": phase,
        "iteration": iteration,
        "status": "done",
        "next_recommended_phase": "<phase>",
    }
    data.update(PHASE_OUTPUT_EXTRAS.get(phase, {}))
    return data


def _extract_json_objects(text: str | None) -> list[dict[str, Any]]:
    if not text:
        return []
    candidates = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    objects: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            objects.append(parsed)
    return objects


def _extract_json_object(text: str | None) -> dict[str, Any]:
    objects = _extract_json_objects(text)
    return objects[0] if objects else {}


def _task_meta(task: kb.Task) -> dict[str, Any]:
    for obj in _extract_json_objects(task.body):
        if obj.get("workflow_type") == WORKFLOW_TYPE:
            return obj
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


def _clean_pr_token(token: str) -> str:
    value = (token or "").strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    if "|" in value and value.startswith(("http://", "https://")):
        value = value.split("|", 1)[0]
    return value.strip()


def parse_pr_review_request(
    raw_args: str,
    *,
    default_repo_slug: str = PR_REVIEW_DEFAULT_REPO_SLUG,
) -> tuple[PullRequestRef, str]:
    """Parse ``/pr-review`` args into a PR reference and optional focus text."""
    raw = (raw_args or "").strip()
    if not raw:
        raise ValueError("Usage: /pr-review <PR_URL|PR_NUMBER> [extra review focus]")

    if raw.startswith("<") and ">" in raw:
        end = raw.index(">")
        token = raw[: end + 1]
        focus = raw[end + 1 :].strip()
    else:
        try:
            tokens = shlex.split(raw)
        except ValueError as exc:
            raise ValueError(f"invalid /pr-review arguments: {exc}") from exc
        if not tokens:
            raise ValueError("Usage: /pr-review <PR_URL|PR_NUMBER> [extra review focus]")
        token = tokens[0]
        focus = " ".join(tokens[1:]).strip()

    token = _clean_pr_token(token)
    if re.fullmatch(r"\d+", token):
        owner, repo_name = default_repo_slug.split("/", 1)
        number = int(token)
        return (
            PullRequestRef(
                owner=owner,
                repo_name=repo_name,
                number=number,
                url=f"https://github.com/{owner}/{repo_name}/pull/{number}",
            ),
            focus,
        )

    match = re.match(
        r"^(?:https?://)?github\.com/([^/]+)/([^/]+)/pull/(\d+)(?:[/?#].*)?$",
        token,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError("Usage: /pr-review <PR_URL|PR_NUMBER> [extra review focus]")
    owner, repo_name, number_text = match.groups()
    repo_name = repo_name.removesuffix(".git")
    number = int(number_text)
    return (
        PullRequestRef(
            owner=owner,
            repo_name=repo_name,
            number=number,
            url=f"https://github.com/{owner}/{repo_name}/pull/{number}",
        ),
        focus,
    )


def _load_pr_metadata(pr: PullRequestRef) -> dict[str, Any]:
    fields = ",".join(
        [
            "number",
            "url",
            "title",
            "body",
            "author",
            "baseRefName",
            "headRefName",
            "headRefOid",
            "isDraft",
            "mergeStateStatus",
            "reviewDecision",
            "statusCheckRollup",
        ]
    )
    try:
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pr.number),
                "--repo",
                pr.repo_slug,
                "--json",
                fields,
            ],
            text=True,
            capture_output=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise ValueError("gh CLI is required for /pr-review") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise ValueError(f"failed to read PR #{pr.number}: {detail or exc}") from exc

    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"gh returned invalid JSON for PR #{pr.number}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"gh returned unexpected PR payload for #{pr.number}")
    data["number"] = int(data.get("number") or pr.number)
    data["url"] = data.get("url") or pr.url
    return data


def _pr_review_branch(pr_number: int, head_sha: str | None, head_ref: str | None) -> str:
    suffix_source = (head_sha or head_ref or "unknown").strip()
    suffix = _slugify(suffix_source)[:12] or "unknown"
    return f"codex/pr-review-{pr_number}-{suffix}"


def _matching_branch_worktree(repo: Path, branch: str) -> tuple[str, str | None] | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
            text=True,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return None
    for entry in _parse_worktree_list(proc.stdout):
        if _branch_short(entry.get("branch")) != branch:
            continue
        raw_path = entry.get("worktree")
        if raw_path:
            return str(Path(raw_path).expanduser().resolve()), branch
    return None


def _prepare_pr_review_worktree(
    *,
    repo: Path,
    pr_number: int,
    base_branch: str,
    head_sha: str | None,
    head_ref: str | None,
) -> WorktreeResolution:
    branch = _pr_review_branch(pr_number, head_sha, head_ref)
    existing = _matching_branch_worktree(repo, branch)
    if existing:
        path, branch_name = existing
        worktree_path = Path(path).resolve()
        return WorktreeResolution(
            repo=str(repo),
            worktree=str(worktree_path),
            branch=branch_name,
            topic=f"pr-review-{pr_number}",
            topic_source="pr_review",
            source="existing",
            resources=_worktree_resources(worktree_path),
        )

    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "fetch",
            "origin",
            f"+pull/{pr_number}/head:refs/heads/{branch}",
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    script = repo / "scripts" / "codex-worktree.sh"
    if not script.exists():
        raise ValueError(f"worktree bootstrap script not found: {script}")
    subprocess.run(
        [str(script), "create", branch, base_branch, "--no-codex"],
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=True,
    )
    worktree = _default_worktree_root(repo) / branch.replace("/", "-")
    if not worktree.exists():
        raise ValueError(f"worktree script completed but path was not created: {worktree}")
    return WorktreeResolution(
        repo=str(repo),
        worktree=str(worktree.resolve()),
        branch=branch,
        topic=f"pr-review-{pr_number}",
        topic_source="pr_review",
        source="created",
        resources=_worktree_resources(worktree),
    )


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
    current_phase: str = "spec_discussion",
    iteration: int = 1,
    status: str = "running",
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
        "current_phase": current_phase,
        "iteration": iteration,
        "status": status,
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


def _root_body_from_meta(meta: dict[str, Any]) -> str:
    return _root_body(
        issue=meta.get("issue"),
        repo=meta.get("repo"),
        worktree=meta.get("worktree"),
        base_branch=meta.get("base_branch") or "master",
        branch=meta.get("branch"),
        topic=meta.get("topic"),
        topic_source=meta.get("topic_source"),
        worktree_source=meta.get("worktree_source") or "unknown",
        worktree_resources=meta.get("worktree_resources") or {},
        current_phase=meta.get("current_phase") or "spec_discussion",
        iteration=int(meta.get("iteration") or 1),
        status=meta.get("status") or "running",
    )


def _update_root_progress(
    conn,
    root_task_id: str,
    root_meta: dict[str, Any],
    *,
    current_phase: str,
    iteration: int,
    status: str | None = None,
) -> None:
    next_meta = dict(root_meta)
    next_meta["current_phase"] = current_phase
    next_meta["iteration"] = iteration
    if status is not None:
        next_meta["status"] = status
    with kb.write_txn(conn):
        conn.execute(
            "UPDATE tasks SET body = ? WHERE id = ?",
            (_root_body_from_meta(next_meta), root_task_id),
        )
        conn.execute(
            "INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, 'edited', ?, strftime('%s','now'))",
            (
                root_task_id,
                json.dumps(
                    {
                        "source": WORKFLOW_PLUGIN_AUTHOR,
                        "reason": "update workflow progress",
                        "current_phase": current_phase,
                        "iteration": iteration,
                    },
                    ensure_ascii=False,
                ),
            ),
        )


def _artifact_dir(issue: int | None) -> str:
    return f"hedge_docs/tasks/issue-{issue}" if issue else "hedge_docs/tasks"


def _phase_prompt_artifacts(phase: str, issue: int | None) -> list[str]:
    base = _artifact_dir(issue)
    mapping = {
        "spec_freeze": [f"{base}/discussion.md"],
        "blueprint_prompts": [f"{base}/spec.md"],
        "spec_blueprint_review": [f"{base}/spec.md", f"{base}/blueprint.md"],
        "implementation": [f"{base}/spec.md", f"{base}/blueprint.md"],
        "ci_watch_repair": [f"{base}/implementation-report.md"],
        "branch_sync_repair": [f"{base}/implementation-report.md", f"{base}/ci-report.md"],
        "local_code_review": [f"{base}/spec.md", f"{base}/blueprint.md", f"{base}/ci-report.md"],
        "pr_review_followup": [f"{base}/local-code-review.md"],
        "closeout_sync": [f"{base}/pr-review-followup.md"],
        "i18n_check": [f"{base}/implementation-report.md"],
        "project_followup": [f"{base}/spec-blueprint-review.md", f"{base}/local-code-review.md"],
    }
    return mapping.get(phase, [])


def _child_body(
    *,
    root: kb.Task,
    root_meta: dict[str, Any],
    phase: str,
    iteration: int,
    parent_task_id: str | None,
) -> str:
    assignee = PHASE_ASSIGNEE[phase]
    issue = root_meta.get("issue")
    execution_backend = PHASE_EXECUTION_BACKEND.get(phase, "hermes_worker")
    skill = PHASE_SKILL.get(phase)
    data = {
        "workflow_type": WORKFLOW_TYPE,
        "workflow_id": root_meta["workflow_id"],
        "root_task_id": root.id,
        "phase": phase,
        "iteration": iteration,
        "assignee_role": assignee.replace("odoo-hedge-", ""),
        "issue": issue,
        "repo": root_meta.get("repo"),
        "worktree": root_meta.get("worktree"),
        "branch": root_meta.get("branch"),
        "topic": root_meta.get("topic"),
        "base_branch": root_meta.get("base_branch"),
        "worktree_resources": root_meta.get("worktree_resources") or {},
        "parent_task_id": parent_task_id,
        "execution_backend": execution_backend,
        "skill": skill,
        "prompt_artifacts": _phase_prompt_artifacts(phase, issue),
        "allowed_actions": PHASE_ALLOWED_ACTIONS.get(phase, []),
        "forbidden_actions": PHASE_FORBIDDEN_ACTIONS.get(phase, []),
        "expected_output_schema": PHASE_SCHEMA[phase],
    }
    lines = [
        f"Odoo Hedge workflow child task: `{phase}` iteration {iteration}.",
        "",
        "请严格遵守 metadata 中的 allowed_actions / forbidden_actions。",
    ]
    instructions = PHASE_INSTRUCTIONS.get(phase) or []
    if instructions:
        lines.extend(["", "Phase-specific instructions:"])
        lines.extend(f"- {item}" for item in instructions)
    lines.extend(
        [
            "",
            "完成时必须在 task summary/result 中包含一个 JSON object，字段至少包括：",
            "",
            _json_block(_phase_output_example(root_meta, phase, iteration)),
            "",
            "Task metadata:",
            "",
            _json_block(data),
        ]
    )
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
        if status == "blocked":
            conn.execute(
                "INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, 'blocked', ?, strftime('%s','now'))",
                (
                    task_id,
                    json.dumps(
                        {
                            "source": WORKFLOW_PLUGIN_AUTHOR,
                            "reason": "workflow root is controlled by odoo-hedge-workflow tick",
                        },
                        ensure_ascii=False,
                    ),
                ),
            )


def _add_root_notification_subscription(
    conn,
    root_task_id: str,
    *,
    platform: str | None = None,
    chat_id: str | None = None,
    thread_id: str | None = None,
    user_id: str | None = None,
    notifier_profile: str | None = None,
    skip_existing_events: bool = True,
) -> dict[str, str] | None:
    platform_value = (platform or "").strip().lower()
    chat_value = (chat_id or "").strip()
    if not platform_value and not chat_value:
        return None
    if not platform_value or not chat_value:
        raise ValueError("--notify-platform and --notify-chat-id must be provided together")

    thread_value = (thread_id or "").strip()
    user_value = (user_id or "").strip()
    notifier_value = (notifier_profile or "").strip()
    existing = conn.execute(
        """
        SELECT 1 FROM kanban_notify_subs
         WHERE task_id = ?
           AND platform = ?
           AND chat_id = ?
           AND thread_id = ?
         LIMIT 1
        """,
        (root_task_id, platform_value, chat_value, thread_value),
    ).fetchone()
    kb.add_notify_sub(
        conn,
        task_id=root_task_id,
        platform=platform_value,
        chat_id=chat_value,
        thread_id=thread_value or None,
        user_id=user_value or None,
        notifier_profile=notifier_value or None,
    )
    target = {
        "platform": platform_value,
        "chat_id": chat_value,
        "thread_id": thread_value,
        "user_id": user_value,
        "notifier_profile": notifier_value,
    }
    if existing is None:
        kb.add_comment(
            conn,
            root_task_id,
            WORKFLOW_PLUGIN_AUTHOR,
            "\n".join(
                [
                    "workflow_notification_target: true",
                    f"platform: {platform_value}",
                    f"chat_id: {chat_value}",
                    f"thread_id: {thread_value}",
                    f"user_id: {user_value}",
                    f"notifier_profile: {notifier_value}",
                ]
            ),
        )
    if skip_existing_events and existing is None:
        row = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS max_id FROM task_events WHERE task_id = ?",
            (root_task_id,),
        ).fetchone()
        cursor = int(row["max_id"] if row else 0)
        with kb.write_txn(conn):
            conn.execute(
                """
                UPDATE kanban_notify_subs
                   SET last_event_id = CASE
                       WHEN last_event_id < ? THEN ?
                       ELSE last_event_id
                   END
                 WHERE task_id = ?
                   AND platform = ?
                   AND chat_id = ?
                   AND thread_id = ?
                """,
                (
                    cursor,
                    cursor,
                    root_task_id,
                    platform_value,
                    chat_value,
                    thread_value,
                ),
            )
    return target


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
    notify_platform: str | None = None,
    notify_chat_id: str | None = None,
    notify_thread_id: str | None = None,
    notify_user_id: str | None = None,
    notifier_profile: str | None = None,
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
            phase="spec_discussion",
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
                    "next_phase: spec_discussion",
                    f"worktree_source: {resolved.source}",
                    f"worktree: {resolved.worktree}",
                    f"branch: {resolved.branch or ''}",
                    f"topic: {resolved.topic or ''}",
                ]
            ),
        )
        notification_target = _add_root_notification_subscription(
            conn,
            root_id,
            platform=notify_platform,
            chat_id=notify_chat_id,
            thread_id=notify_thread_id,
            user_id=notify_user_id,
            notifier_profile=notifier_profile,
        )
    return {
        "kind": "start",
        "board": board,
        "root_task_id": root_id,
        "child_task_id": child_id,
        "child_phase": "spec_discussion",
        "branch": resolved.branch,
        "topic": resolved.topic,
        "topic_source": resolved.topic_source,
        "worktree": resolved.worktree,
        "worktree_source": resolved.source,
        "worktree_resources": resolved.resources,
        "notification_target": notification_target,
    }


def _pr_review_task_body(
    *,
    metadata: dict[str, Any],
    review_focus: str,
) -> str:
    pr = metadata.get("pr") or {}
    lines = [
        f"Odoo Hedge PR review task for PR #{pr.get('number')}.",
        "",
        "该 task 是一次性 PR review，不属于完整 issue delivery workflow。",
        "它只执行 local_code_review，不由 odoo-hedge-workflow tick 推进下一阶段。",
        "",
        "Review focus:",
        review_focus or "(none)",
        "",
        "Task metadata:",
        "",
        _json_block(metadata),
    ]
    return "\n".join(lines)


def start_pr_review(
    raw_args: str,
    *,
    board: str = PR_REVIEW_DEFAULT_BOARD,
    repo: str = PR_REVIEW_DEFAULT_REPO,
    requester: str | None = None,
) -> dict[str, Any]:
    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.exists():
        raise ValueError(f"repo path does not exist: {repo_path}")

    repo_slug = _github_repo_slug(repo_path) or PR_REVIEW_DEFAULT_REPO_SLUG
    pr_ref, review_focus = parse_pr_review_request(
        raw_args,
        default_repo_slug=repo_slug,
    )
    if pr_ref.repo_slug.casefold() != repo_slug.casefold():
        raise ValueError(
            f"/pr-review only supports {repo_slug}; got {pr_ref.repo_slug}"
        )

    pr_data = _load_pr_metadata(pr_ref)
    base_branch = str(pr_data.get("baseRefName") or "master")
    head_ref = str(pr_data.get("headRefName") or "")
    head_sha = str(pr_data.get("headRefOid") or "")
    resolved = _prepare_pr_review_worktree(
        repo=repo_path,
        pr_number=pr_ref.number,
        base_branch=base_branch,
        head_sha=head_sha,
        head_ref=head_ref,
    )

    workflow_id = f"pr-review-{pr_ref.number}-{(head_sha or head_ref or 'unknown')[:12]}"
    metadata = {
        "workflow_type": WORKFLOW_TYPE,
        "workflow_id": workflow_id,
        "entry": PR_REVIEW_ENTRY,
        "review_only": True,
        "root_task_id": None,
        "phase": "local_code_review",
        "iteration": 1,
        "assignee_role": "code-reviewer",
        "issue": None,
        "repo": str(repo_path),
        "repo_slug": repo_slug,
        "worktree": resolved.worktree,
        "branch": resolved.branch,
        "base_branch": base_branch,
        "worktree_source": resolved.source,
        "worktree_resources": resolved.resources,
        "parent_task_id": None,
        "execution_backend": "codex_exec",
        "skill": None,
        "prompt_artifacts": [],
        "allowed_actions": PHASE_ALLOWED_ACTIONS["local_code_review"],
        "forbidden_actions": PHASE_FORBIDDEN_ACTIONS["local_code_review"],
        "expected_output_schema": PHASE_SCHEMA["local_code_review"],
        "review_focus": review_focus,
        "requester": requester or "",
        "pr": {
            "number": pr_ref.number,
            "url": pr_data.get("url") or pr_ref.url,
            "title": pr_data.get("title") or "",
            "body": pr_data.get("body") or "",
            "author": pr_data.get("author") or {},
            "baseRefName": base_branch,
            "headRefName": head_ref,
            "headRefOid": head_sha,
            "isDraft": bool(pr_data.get("isDraft")),
            "mergeStateStatus": pr_data.get("mergeStateStatus") or "",
            "reviewDecision": pr_data.get("reviewDecision") or "",
            "statusCheckRollup": pr_data.get("statusCheckRollup") or [],
        },
    }
    body = _pr_review_task_body(metadata=metadata, review_focus=review_focus)
    title_suffix = str(pr_data.get("title") or "").strip()
    title = f"PR #{pr_ref.number}: local code review"
    if title_suffix:
        title = f"{title} - {title_suffix[:90]}"

    idempotency_head = head_sha or head_ref or "unknown"
    with kb.connect_closing(board=board) as conn:
        task_id = kb.create_task(
            conn,
            title=title,
            body=body,
            assignee=CODE_REVIEWER,
            created_by=WORKFLOW_PLUGIN_AUTHOR,
            workspace_kind="worktree",
            workspace_path=resolved.worktree,
            branch_name=resolved.branch,
            priority=0,
            idempotency_key=(
                f"{WORKFLOW_TYPE}:{PR_REVIEW_ENTRY}:{repo_slug}:"
                f"{pr_ref.number}:{idempotency_head}"
            ),
            board=board,
        )
        kb.add_comment(
            conn,
            task_id,
            WORKFLOW_PLUGIN_AUTHOR,
            "\n".join(
                [
                    "pr_review_started: true",
                    f"pr: {repo_slug}#{pr_ref.number}",
                    f"worktree: {resolved.worktree}",
                    f"branch: {resolved.branch or ''}",
                    f"requester: {requester or ''}",
                    f"review_focus: {review_focus}",
                ]
            ),
        )

    return {
        "kind": "pr_review",
        "board": board,
        "task_id": task_id,
        "pr_number": pr_ref.number,
        "pr_url": metadata["pr"]["url"],
        "pr_title": metadata["pr"]["title"],
        "repo": str(repo_path),
        "repo_slug": repo_slug,
        "worktree": resolved.worktree,
        "worktree_source": resolved.source,
        "branch": resolved.branch,
        "base_branch": base_branch,
        "head_sha": head_sha,
        "review_focus": review_focus,
    }


def _normalized_approval(result: dict[str, Any], *, positive_next_phases: set[str]) -> bool | None:
    approved = result.get("approved")
    if isinstance(approved, bool):
        return approved

    for key in ("review_decision", "decision", "verdict"):
        raw = result.get(key)
        if not raw:
            continue
        value = str(raw).strip().casefold()
        negative_tokens = (
            "changes_requested",
            "change_requested",
            "request_changes",
            "requested_changes",
            "rejected",
            "blocked",
            "failed",
            "not approved",
            "not_approved",
        )
        if any(token in value for token in negative_tokens):
            return False
        if any(token in value for token in ("approved", "approve", "pass", "passed", "accepted")):
            return True

    recommended = str(result.get("next_recommended_phase") or "").strip().casefold()
    if recommended in {phase.casefold() for phase in positive_next_phases}:
        return True
    return None


def _pr_review_notification(metadata: dict[str, Any], task_meta: dict[str, Any]) -> dict[str, Any]:
    approved = _normalized_approval(metadata, positive_next_phases=set())
    if approved is True:
        decision = "approve"
    elif approved is False:
        decision = "request changes"
    else:
        raw_decision = str(metadata.get("review_decision") or "").strip().replace("_", " ")
        decision = raw_decision or "unknown"

    pr = task_meta.get("pr") if isinstance(task_meta.get("pr"), dict) else {}
    link = str(metadata.get("pr_comment_url") or pr.get("url") or "").strip()
    lines = [f"PR review result: {decision}"]
    if link:
        lines.append(f"GitHub: {link}")
    return {
        "message": "\n".join(lines),
        "skip_artifacts": True,
    }


def _should_process_no_next(reason: str) -> bool:
    lowered = reason.casefold()
    if "missing" in lowered:
        return False
    if "needs user input" in lowered:
        return False
    return True


def _needs_branch_sync(result: dict[str, Any]) -> bool:
    recommended = str(result.get("next_recommended_phase") or "").strip().casefold()
    if recommended in {"branch_sync_repair", "branch-sync-repair", "branch sync repair"}:
        return True

    mergeable = str(result.get("mergeable") or "").strip().casefold()
    merge_state = str(result.get("merge_state_status") or result.get("mergeStateStatus") or "").strip().casefold()
    if mergeable in {"conflicting", "unknown"}:
        return True
    if merge_state in {"dirty", "blocked", "unknown", "unstable", "behind"}:
        return True

    text_parts: list[str] = []
    for key in ("blockers", "warnings", "notes", "summary"):
        value = result.get(key)
        if isinstance(value, list):
            text_parts.extend(str(item) for item in value)
        elif value:
            text_parts.append(str(value))
    text = "\n".join(text_parts).casefold()
    return any(
        token in text
        for token in (
            "mergeable=conflicting",
            "mergestatestatus=dirty",
            "merge state status=dirty",
            "merge conflict",
            "needs branch sync",
            "branch sync",
            "rebase",
        )
    )


def _next_from_child(child: kb.Task) -> tuple[str | None, int, str]:
    meta = _task_meta(child)
    phase = meta.get("phase")
    iteration = int(meta.get("iteration") or 1)
    result = _result_meta(child)

    if result.get("needs_user_input") is True:
        return None, iteration, f"{phase} needs user input"
    if result.get("needs_followup_issue") is True or result.get("requires_followup_issue") is True:
        return "project_followup", iteration, f"{phase} requested a follow-up issue"
    if result.get("requires_i18n") is True:
        return "i18n_check", iteration, f"{phase} requested i18n check"

    if phase == "spec_discussion":
        return "spec_freeze", iteration, "spec discussion is ready to freeze"
    if phase == "spec_freeze":
        return "blueprint_prompts", iteration, "frozen spec is ready for blueprint"
    if phase == "blueprint_prompts":
        return "spec_blueprint_review", iteration, "blueprint is ready for review"
    if phase == "spec_blueprint_review":
        approved = _normalized_approval(result, positive_next_phases={"implementation", "coder_implementation"})
        if approved is True:
            return "implementation", iteration, "spec/blueprint review approved"
        if approved is False:
            return_phase = result.get("return_phase") or "blueprint_prompts"
            if return_phase not in {"spec_freeze", "blueprint_prompts"}:
                return_phase = "blueprint_prompts"
            return return_phase, iteration + 1, "spec/blueprint review requested changes"
        return None, iteration, "spec_blueprint_review result missing approved=true/false"
    if phase == "implementation":
        return "ci_watch_repair", iteration, "implementation is ready for CI"
    if phase == "ci_watch_repair":
        if _needs_branch_sync(result):
            return "branch_sync_repair", iteration, "CI passed but PR branch needs sync before review"
        if result.get("success") is True:
            return "local_code_review", iteration, "CI passed"
        if result.get("success") is False or result.get("ci_failed") is True:
            return "implementation", iteration + 1, "CI requested fixes"
        return None, iteration, "ci_watch_repair result missing success=true/false"
    if phase == "branch_sync_repair":
        if result.get("success") is True or result.get("synced") is True:
            return "ci_watch_repair", iteration, "branch sync completed; rerun CI"
        if result.get("retry_branch_sync") is True:
            return "branch_sync_repair", iteration + 1, "branch sync was not completed; retry branch sync"
        if result.get("success") is False or result.get("conflicts_resolved") is False:
            return "implementation", iteration + 1, "branch sync needs implementation fixes"
        return None, iteration, "branch_sync_repair result missing success=true/false"
    if phase == "local_code_review":
        approved = _normalized_approval(result, positive_next_phases={"pr_review_followup"})
        if approved is True:
            return "pr_review_followup", iteration, "local code review approved"
        if approved is False:
            return "implementation", iteration + 1, "local code review requested changes"
        return None, iteration, "local_code_review result missing approved=true/false"
    if phase == "pr_review_followup":
        if result.get("comments_resolved") is True:
            return "closeout_sync", iteration, "PR comments resolved"
        if result.get("comments_resolved") is False:
            return "implementation", iteration + 1, "PR comments requested changes"
        return None, iteration, "pr_review_followup result missing comments_resolved=true/false"
    if phase == "i18n_check":
        return "ci_watch_repair", iteration, "i18n check completed"
    if phase == "project_followup":
        return result.get("return_phase") or "implementation", iteration, "follow-up issue sync completed"
    if phase == "closeout_sync":
        missing: list[str] = []
        for key in ("closeout_synced", "pr_merged", "issue_closed"):
            if not isinstance(result.get(key), bool):
                missing.append(f"{key}=true/false")
        if not str(result.get("project_status") or "").strip():
            missing.append("project_status")
        if missing:
            return None, iteration, "closeout_sync result missing " + ", ".join(missing)
        return None, iteration, "closeout completed"
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
            next_phase = "spec_discussion"
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
                process_child = _should_process_no_next(reason)
                processed_meta = _task_meta(processed_child)
                _update_root_progress(
                    conn,
                    root_task_id,
                    root_meta,
                    current_phase=processed_meta.get("phase") or root_meta.get("current_phase") or "unknown",
                    iteration=int(processed_meta.get("iteration") or root_meta.get("iteration") or 1),
                    status="waiting",
                )
                kb.add_comment(
                    conn,
                    root_task_id,
                    WORKFLOW_PLUGIN_AUTHOR,
                    "\n".join(
                        [
                            (
                                f"processed_child_task: {processed_child.id}"
                                if process_child
                                else f"pending_child_task: {processed_child.id}"
                            ),
                            f"verdict: {reason}",
                            "created_child_task: none",
                        ]
                    ),
                )
            return {
                "kind": "tick",
                "action": "no_next_task",
                "reason": reason,
                "processed_child_task": (
                    processed_child.id
                    if processed_child and _should_process_no_next(reason)
                    else None
                ),
                "unprocessed_child_task": (
                    processed_child.id
                    if processed_child and not _should_process_no_next(reason)
                    else None
                ),
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
        _update_root_progress(
            conn,
            root_task_id,
            root_meta,
            current_phase=next_phase,
            iteration=next_iteration,
            status="running",
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
                "execution_backend": meta.get("execution_backend"),
                "skill": meta.get("skill"),
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


def _root_event(conn, task_id: str, kind: str, payload: dict[str, Any] | None = None) -> None:
    conn.execute(
        "INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, ?, ?, strftime('%s','now'))",
        (
            task_id,
            kind,
            json.dumps(payload, ensure_ascii=False) if payload else None,
        ),
    )


def _pause_workflow_root(
    root_task_id: str,
    *,
    board: str,
    reason: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = f"Workflow paused: {reason}"
    payload = {
        "source": WORKFLOW_PLUGIN_AUTHOR,
        "reason": reason,
        "details": details or {},
    }
    with kb.connect_closing(board=board) as conn:
        root = _load_task(conn, root_task_id)
        root_meta = _validate_root(root)
        _update_root_progress(
            conn,
            root_task_id,
            root_meta,
            current_phase=root_meta.get("current_phase") or "unknown",
            iteration=int(root_meta.get("iteration") or 1),
            status="paused",
        )
        kb.add_comment(
            conn,
            root_task_id,
            WORKFLOW_PLUGIN_AUTHOR,
            "\n".join(
                [
                    "workflow_auto_paused: true",
                    f"reason: {reason}",
                    "details:",
                    _json_block(details or {}),
                ]
            ),
        )
        with kb.write_txn(conn):
            conn.execute(
                """
                UPDATE tasks
                   SET status = 'blocked',
                       result = ?,
                       claim_lock = NULL,
                       claim_expires = NULL,
                       worker_pid = NULL
                 WHERE id = ?
                   AND status NOT IN ('done', 'archived')
                """,
                (summary, root_task_id),
            )
            _root_event(conn, root_task_id, "blocked", payload)
    return {"root_status": "blocked", "notified": True, "summary": summary}


def _complete_workflow_root(
    root_task_id: str,
    *,
    board: str,
    reason: str,
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = f"Workflow completed: {reason}"
    metadata = {
        "workflow_type": WORKFLOW_TYPE,
        "source": WORKFLOW_PLUGIN_AUTHOR,
        "outcome": "completed",
        "reason": reason,
        "auto_controller": True,
        "steps": len(actions),
    }
    with kb.connect_closing(board=board) as conn:
        root = _load_task(conn, root_task_id)
        root_meta = _validate_root(root)
        _update_root_progress(
            conn,
            root_task_id,
            root_meta,
            current_phase=root_meta.get("current_phase") or "closeout_sync",
            iteration=int(root_meta.get("iteration") or 1),
            status="completed",
        )
        kb.add_comment(
            conn,
            root_task_id,
            WORKFLOW_PLUGIN_AUTHOR,
            "\n".join(
                [
                    "workflow_auto_completed: true",
                    f"reason: {reason}",
                    f"steps: {len(actions)}",
                ]
            ),
        )
        completed = kb.complete_task(
            conn,
            root_task_id,
            result=summary,
            summary=summary,
            metadata=metadata,
        )
    return {"root_status": "done" if completed else "unchanged", "notified": completed, "summary": summary}


def _wait_pause_details(root_task_id: str, *, board: str) -> dict[str, Any] | None:
    status = workflow_status(root_task_id, board=board)
    active = [
        child for child in status.get("children") or []
        if child.get("status") in {"ready", "todo", "running", "blocked", "review"}
    ]
    blocked = [child for child in active if child.get("status") == "blocked"]
    if blocked:
        return {
            "reason": f"active child {blocked[0]['id']} is blocked",
            "active_children": active,
            "blocked_children": blocked,
        }
    if not active:
        return {
            "reason": "workflow has no active child task and no completed unprocessed child",
            "active_children": active,
        }
    return None


def _is_workflow_complete_reason(reason: str | None) -> bool:
    return "closeout completed" in (reason or "").casefold()


def _is_manual_judgement_reason(reason: str | None) -> bool:
    lowered = (reason or "").casefold()
    return (
        "missing" in lowered
        or "needs user input" in lowered
        or "unsupported child phase" in lowered
    )


def run_auto_workflow(
    *,
    root_task_id: str,
    board: str,
    poll_seconds: float = AUTO_DEFAULT_POLL_SECONDS,
    max_steps: int = AUTO_DEFAULT_MAX_STEPS,
    notify_platform: str | None = None,
    notify_chat_id: str | None = None,
    notify_thread_id: str | None = None,
    notify_user_id: str | None = None,
    notifier_profile: str | None = None,
) -> dict[str, Any]:
    if max_steps <= 0:
        raise ValueError("max_steps must be positive")
    if poll_seconds < 0:
        raise ValueError("poll_seconds must be >= 0")

    with kb.connect_closing(board=board) as conn:
        root = _load_task(conn, root_task_id)
        _validate_root(root)
        notification_target = _add_root_notification_subscription(
            conn,
            root_task_id,
            platform=notify_platform,
            chat_id=notify_chat_id,
            thread_id=notify_thread_id,
            user_id=notify_user_id,
            notifier_profile=notifier_profile,
        )
        kb.add_comment(
            conn,
            root_task_id,
            WORKFLOW_PLUGIN_AUTHOR,
            "\n".join(
                [
                    "workflow_auto_started: true",
                    f"poll_seconds: {poll_seconds}",
                    f"max_steps: {max_steps}",
                ]
            ),
        )

    actions: list[dict[str, Any]] = []
    for step in range(1, max_steps + 1):
        result = tick_workflow(root_task_id=root_task_id, board=board, apply=True)
        action_record = {
            "step": step,
            "action": result.get("action"),
            "reason": result.get("reason"),
            "processed_child_task": result.get("processed_child_task"),
            "unprocessed_child_task": result.get("unprocessed_child_task"),
            "next_phase": result.get("next_phase"),
            "created_task_id": result.get("created_task_id"),
            "active_children": result.get("active_children") or [],
        }
        actions.append(action_record)

        if result.get("action") == "create_next_task":
            continue

        if result.get("action") == "wait":
            pause_details = _wait_pause_details(root_task_id, board=board)
            if pause_details:
                pause = _pause_workflow_root(
                    root_task_id,
                    board=board,
                    reason=pause_details["reason"],
                    details=pause_details,
                )
                return {
                    "kind": "auto",
                    "board": board,
                    "root_task_id": root_task_id,
                    "outcome": "paused",
                    "reason": pause_details["reason"],
                    "steps": step,
                    "actions": actions,
                    "notification_target": notification_target,
                    "root_update": pause,
                }
            if poll_seconds:
                time.sleep(poll_seconds)
            continue

        if result.get("action") == "no_next_task":
            reason = str(result.get("reason") or "")
            if _is_workflow_complete_reason(reason):
                complete = _complete_workflow_root(
                    root_task_id,
                    board=board,
                    reason=reason,
                    actions=actions,
                )
                return {
                    "kind": "auto",
                    "board": board,
                    "root_task_id": root_task_id,
                    "outcome": "completed",
                    "reason": reason,
                    "steps": step,
                    "actions": actions,
                    "notification_target": notification_target,
                    "root_update": complete,
                }
            if result.get("unprocessed_child_task") or _is_manual_judgement_reason(reason):
                pause = _pause_workflow_root(
                    root_task_id,
                    board=board,
                    reason=reason or "workflow needs manual judgement",
                    details=result,
                )
                return {
                    "kind": "auto",
                    "board": board,
                    "root_task_id": root_task_id,
                    "outcome": "paused",
                    "reason": reason,
                    "steps": step,
                    "actions": actions,
                    "notification_target": notification_target,
                    "root_update": pause,
                }
            pause = _pause_workflow_root(
                root_task_id,
                board=board,
                reason=reason or "workflow has no next task",
                details=result,
            )
            return {
                "kind": "auto",
                "board": board,
                "root_task_id": root_task_id,
                "outcome": "paused",
                "reason": reason,
                "steps": step,
                "actions": actions,
                "notification_target": notification_target,
                "root_update": pause,
            }

        pause = _pause_workflow_root(
            root_task_id,
            board=board,
            reason=f"unexpected tick action {result.get('action')!r}",
            details=result,
        )
        return {
            "kind": "auto",
            "board": board,
            "root_task_id": root_task_id,
            "outcome": "paused",
            "reason": f"unexpected tick action {result.get('action')!r}",
            "steps": step,
            "actions": actions,
            "notification_target": notification_target,
            "root_update": pause,
        }

    return {
        "kind": "auto",
        "board": board,
        "root_task_id": root_task_id,
        "outcome": "timeout",
        "reason": f"auto controller reached max_steps={max_steps}",
        "steps": max_steps,
        "actions": actions,
        "notification_target": notification_target,
    }


def _hermes_argv() -> list[str]:
    return [sys.executable, "-m", "hermes_cli.main"]


def kanban_spawn_override(*, task: kb.Task, workspace: str, board: str | None = None, **_: Any) -> int | None:
    meta = _task_meta(task)
    if meta.get("workflow_type") != WORKFLOW_TYPE:
        return None
    if meta.get("execution_backend") != "codex_exec":
        return None

    resolved_board = board or kb.get_current_board()
    env = dict(os.environ)
    env["HERMES_KANBAN_TASK"] = task.id
    env["HERMES_KANBAN_WORKSPACE"] = workspace
    env["HERMES_KANBAN_DB"] = str(kb.kanban_db_path(board=resolved_board))
    env["HERMES_KANBAN_WORKSPACES_ROOT"] = str(kb.workspaces_root(board=resolved_board))
    env["HERMES_KANBAN_BOARD"] = resolved_board
    env["HERMES_PROFILE"] = ORCHESTRATOR
    if task.current_run_id is not None:
        env["HERMES_KANBAN_RUN_ID"] = str(task.current_run_id)
    if task.claim_lock:
        env["HERMES_KANBAN_CLAIM_LOCK"] = task.claim_lock
    if task.branch_name:
        env["HERMES_KANBAN_BRANCH"] = task.branch_name

    log_dir = kb.worker_logs_dir(board=resolved_board)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{task.id}.log"
    rotate_bytes, backup_count = kb.worker_log_rotation_config()
    kb._rotate_worker_log(log_path, rotate_bytes, backup_count)

    cmd = [
        *_hermes_argv(),
        "-p",
        ORCHESTRATOR,
        "--accept-hooks",
        "odoo-hedge-workflow",
        "--board",
        resolved_board,
        "codex-exec-worker",
        task.id,
    ]
    log_f = open(log_path, "ab")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=workspace if os.path.isdir(workspace) else None,
            stdin=subprocess.DEVNULL,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    except Exception:
        log_f.close()
        raise
    return proc.pid


def _codex_exec_prompt(task: kb.Task, meta: dict[str, Any]) -> str:
    phase = meta.get("phase")
    skill = meta.get("skill")
    issue = meta.get("issue")
    pr = meta.get("pr") if isinstance(meta.get("pr"), dict) else {}
    artifacts = meta.get("prompt_artifacts") or []
    artifact_lines = "\n".join(f"- {path}" for path in artifacts) if artifacts else "- (none)"
    instructions = PHASE_INSTRUCTIONS.get(str(phase)) or []
    instruction_lines = "\n".join(f"- {item}" for item in instructions)
    try:
        iteration = int(meta.get("iteration") or 1)
    except (TypeError, ValueError):
        iteration = 1
    issue_line = f"GitHub issue: #{issue}" if issue else "GitHub issue: (not provided)"
    pr_lines: list[str] = []
    if meta.get("entry") == PR_REVIEW_ENTRY or meta.get("review_only") is True:
        pr_lines = [
            "",
            "PR review context:",
            f"- PR: {pr.get('url') or '(unknown)'}",
            f"- PR number: {pr.get('number') or '(unknown)'}",
            f"- PR title: {pr.get('title') or '(unknown)'}",
            f"- Base branch: {pr.get('baseRefName') or meta.get('base_branch') or '(unknown)'}",
            f"- Head branch: {pr.get('headRefName') or '(unknown)'}",
            f"- Head SHA: {pr.get('headRefOid') or '(unknown)'}",
            f"- Review focus: {meta.get('review_focus') or '(none)'}",
            "",
            "Review-only task requirements:",
            "- 只做 review，不修改代码、不提交、不 push、不创建 PR。",
            "- 必须把详细 review 结论和改进建议发送为 top-level PR comment。",
            "- PR comment 必须包含：问题背景、实现概述、需求合理性、实现合理性、UI/UX impact、review 结论、blocking findings、non-blocking findings、后续建议。",
            "- 最终 JSON 必须包含 approved、review_decision、review_summary、blocking_findings、non_blocking_findings、pr_comment_url。",
        ]
    return "\n".join(
        [
            f"你正在执行 odoo-hedge workflow phase: {phase}.",
            issue_line,
            f"Kanban task: {task.id}",
            f"Worktree: {meta.get('worktree')}",
            f"Branch: {meta.get('branch')}",
            *pr_lines,
            "",
            f"请使用 `.codex/skills/{skill}` 完成本阶段。" if skill else "请按 task metadata 完成本阶段。",
            "",
            "输入 artifacts:",
            artifact_lines,
            "",
            "本阶段特别要求:",
            instruction_lines if instruction_lines else "- 按 task metadata 和指定 skill 完成本阶段。",
            "",
            "约束:",
            "- 只能在当前 worktree 中工作，不能修改主 repo 根目录。",
            "- 不要创建下一阶段 Kanban task；workflow 推进只能由 Hermes tick 完成。",
            "- 如果需要用户输入，在最终 JSON 中设置 needs_user_input=true。",
            "- 如果需要 follow-up issue，在最终 JSON 中设置 needs_followup_issue=true。",
            "- 如果新增或修改可见 UI 文案，在最终 JSON 中设置 requires_i18n=true。",
            "",
            "完成时，最后回复必须包含一个 JSON object，字段至少包括:",
            _json_block(_phase_output_example(meta, str(phase), iteration)),
        ]
    )


def _block_current_run(task_id: str, *, board: str, run_id: int | None, reason: str) -> bool:
    with kb.connect_closing(board=board) as conn:
        return kb.block_task(conn, task_id, reason=reason, expected_run_id=run_id)


def _complete_current_run(
    task_id: str,
    *,
    board: str,
    run_id: int | None,
    summary: str,
    metadata: dict[str, Any],
) -> bool:
    with kb.connect_closing(board=board) as conn:
        return kb.complete_task(
            conn,
            task_id,
            result=summary,
            summary=summary,
            metadata=metadata,
            expected_run_id=run_id,
        )


def run_codex_exec_worker(task_id: str, *, board: str) -> dict[str, Any]:
    with kb.connect_closing(board=board) as conn:
        task = _load_task(conn, task_id)
        meta = _task_meta(task)
        run_id = task.current_run_id
        claim_lock = task.claim_lock

    if meta.get("workflow_type") != WORKFLOW_TYPE:
        raise ValueError(f"task {task_id} is not an {WORKFLOW_TYPE} task")
    if meta.get("execution_backend") != "codex_exec":
        raise ValueError(f"task {task_id} does not use execution_backend=codex_exec")
    worktree = Path(str(meta.get("worktree") or task.workspace_path or "")).expanduser().resolve()
    if not worktree.is_dir():
        reason = f"codex_exec worktree does not exist: {worktree}"
        _block_current_run(task_id, board=board, run_id=run_id, reason=reason)
        return {"kind": "codex_exec_worker", "task_id": task_id, "status": "blocked", "reason": reason}

    codex_bin = os.environ.get("ODOO_HEDGE_CODEX_BIN", "codex")
    log_dir = kb.worker_logs_dir(board=board)
    log_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f"{task_id}-last-message-",
        suffix=".txt",
        dir=str(log_dir),
        delete=False,
    ) as tmp:
        last_message_path = Path(tmp.name)

    env = dict(os.environ)

    prompt = _codex_exec_prompt(task, meta)
    cmd = [
        codex_bin,
        "exec",
        "--cd",
        str(worktree),
        "--output-last-message",
        str(last_message_path),
        prompt,
    ]
    print("$ " + shlex.join(cmd), flush=True)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(worktree),
            stdin=subprocess.DEVNULL,
            env=env,
        )
    except Exception as exc:
        reason = f"failed to start codex exec: {exc}"
        _block_current_run(task_id, board=board, run_id=run_id, reason=reason)
        return {"kind": "codex_exec_worker", "task_id": task_id, "status": "blocked", "reason": reason}

    last_heartbeat = 0.0
    while True:
        rc = proc.poll()
        now = time.monotonic()
        if now - last_heartbeat >= 60:
            with kb.connect_closing(board=board) as conn:
                if claim_lock and not kb.heartbeat_claim(conn, task_id, claimer=claim_lock):
                    proc.terminate()
                    reason = "codex_exec worker lost Kanban claim during execution"
                    print(reason, flush=True)
                    return {"kind": "codex_exec_worker", "task_id": task_id, "status": "lost_claim"}
            last_heartbeat = now
        if rc is not None:
            break
        time.sleep(5)

    try:
        summary = last_message_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        summary = ""

    if proc.returncode != 0:
        reason = f"codex exec failed with exit code {proc.returncode}"
        if summary:
            reason = reason + ": " + summary[:1000]
        _block_current_run(task_id, board=board, run_id=run_id, reason=reason)
        return {"kind": "codex_exec_worker", "task_id": task_id, "status": "blocked", "reason": reason}

    metadata = _extract_json_object(summary)
    if not metadata:
        reason = "codex exec completed without summary JSON"
        _block_current_run(task_id, board=board, run_id=run_id, reason=reason)
        return {"kind": "codex_exec_worker", "task_id": task_id, "status": "blocked", "reason": reason}
    if meta.get("entry") == PR_REVIEW_ENTRY or meta.get("review_only") is True:
        required = ("approved", "review_decision", "review_summary", "pr_comment_url")
        missing = [key for key in required if metadata.get(key) in (None, "")]
        if missing:
            reason = "codex exec PR review summary missing required field(s): " + ", ".join(missing)
            _block_current_run(task_id, board=board, run_id=run_id, reason=reason)
            return {"kind": "codex_exec_worker", "task_id": task_id, "status": "blocked", "reason": reason}
        metadata = dict(metadata)
        metadata["notification"] = _pr_review_notification(metadata, meta)

    ok = _complete_current_run(
        task_id,
        board=board,
        run_id=run_id,
        summary=summary,
        metadata=metadata,
    )
    if not ok:
        raise RuntimeError(f"failed to complete Kanban task {task_id}")
    return {
        "kind": "codex_exec_worker",
        "task_id": task_id,
        "status": "done",
        "metadata": metadata,
    }
