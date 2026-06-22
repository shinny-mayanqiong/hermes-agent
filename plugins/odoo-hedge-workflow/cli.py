"""CLI surface for the Odoo Hedge workflow plugin."""

from __future__ import annotations

import argparse
import json
import sys

from . import workflow


def register_cli(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--board",
        default="odoo-hedge-dev",
        help="Kanban board slug. Defaults to odoo-hedge-dev.",
    )
    sub = parser.add_subparsers(dest="workflow_command", required=True)

    p_start = sub.add_parser("start", help="Create a workflow root and first child task")
    p_start.add_argument("--issue", required=True, type=int, help="GitHub issue number")
    p_start.add_argument("--worktree", default=None, help="Existing worktree path")
    p_start.add_argument("--repo", default="/home/user/Repos/odoo-hedge", help="Repository path")
    p_start.add_argument("--base-branch", default="master", help="Base branch")
    p_start.add_argument("--branch", default=None, help="Branch to create when no matching worktree exists")
    p_start.add_argument("--topic", default=None, help="Short topic used for the generated branch name")
    p_start.add_argument("--title", default=None, help="Optional root task title")
    p_start.add_argument(
        "--no-create-worktree",
        action="store_true",
        help="Fail instead of creating a worktree when no matching worktree exists",
    )
    p_start.add_argument("--json", action="store_true", help="Emit JSON")
    p_start.add_argument("--auto", action="store_true", help="Run the auto controller after start")
    p_start.add_argument(
        "--poll-seconds",
        type=float,
        default=workflow.AUTO_DEFAULT_POLL_SECONDS,
        help="Seconds between auto-controller wait polls. Defaults to 30.",
    )
    p_start.add_argument(
        "--max-steps",
        type=int,
        default=workflow.AUTO_DEFAULT_MAX_STEPS,
        help="Maximum auto-controller steps before timing out.",
    )
    p_start.add_argument("--notify-platform", default=None, help="Notification platform, e.g. slack")
    p_start.add_argument("--notify-chat-id", default=None, help="Notification chat/channel id")
    p_start.add_argument("--notify-thread-id", default=None, help="Notification thread id/ts")
    p_start.add_argument("--notify-user-id", default=None, help="Notification user id")
    p_start.add_argument("--notifier-profile", default=None, help="Gateway profile that owns notifications")

    p_tick = sub.add_parser("tick", help="Advance a workflow one controller step")
    p_tick.add_argument("root_task_id", help="Workflow root task id")
    mode = p_tick.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="Only print the planned next action")
    mode.add_argument("--apply", action="store_true", help="Create the planned next task")
    mode.add_argument("--auto", action="store_true", help="Run the auto controller until pause/completion")
    p_tick.add_argument(
        "--poll-seconds",
        type=float,
        default=workflow.AUTO_DEFAULT_POLL_SECONDS,
        help="Seconds between auto-controller wait polls. Defaults to 30.",
    )
    p_tick.add_argument(
        "--max-steps",
        type=int,
        default=workflow.AUTO_DEFAULT_MAX_STEPS,
        help="Maximum auto-controller steps before timing out.",
    )
    p_tick.add_argument("--notify-platform", default=None, help="Notification platform, e.g. slack")
    p_tick.add_argument("--notify-chat-id", default=None, help="Notification chat/channel id")
    p_tick.add_argument("--notify-thread-id", default=None, help="Notification thread id/ts")
    p_tick.add_argument("--notify-user-id", default=None, help="Notification user id")
    p_tick.add_argument("--notifier-profile", default=None, help="Gateway profile that owns notifications")
    p_tick.add_argument("--json", action="store_true", help="Emit JSON")

    p_status = sub.add_parser("status", help="Show workflow status")
    p_status.add_argument("root_task_id", help="Workflow root task id")
    p_status.add_argument("--json", action="store_true", help="Emit JSON")

    p_pr_review = sub.add_parser("pr-review", help="Create a one-shot PR review task")
    p_pr_review.add_argument("--repo", default=workflow.PR_REVIEW_DEFAULT_REPO, help="Repository path")
    p_pr_review.add_argument("--json", action="store_true", help="Emit JSON")
    p_pr_review.add_argument(
        "request",
        nargs=argparse.REMAINDER,
        help="PR URL or PR number plus optional review focus",
    )

    p_worker = sub.add_parser(
        "codex-exec-worker",
        help=argparse.SUPPRESS,
        description="Internal worker entrypoint used by the Kanban dispatcher.",
    )
    p_worker.add_argument("task_id", help="Kanban task id")
    p_worker.add_argument("--json", action="store_true", help="Emit JSON")


def _print_result(result: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    kind = result.get("kind") or "result"
    if kind == "start":
        print(f"Workflow root: {result['root_task_id']}")
        print(f"First child:   {result['child_task_id']} ({result['child_phase']})")
        print(f"Board:         {result['board']}")
        print(f"Branch:        {result.get('branch') or '(unknown)'}")
        print(f"Topic:         {result.get('topic') or '(none)'}")
        print(f"Worktree:      {result.get('worktree') or '(unknown)'}")
        print(f"Worktree mode: {result.get('worktree_source') or '(unknown)'}")
        if result.get("notification_target"):
            target = result["notification_target"]
            print(
                "Notify:       "
                f"{target.get('platform')}:{target.get('chat_id')}"
                + (f":{target.get('thread_id')}" if target.get("thread_id") else "")
            )
        return
    if kind == "auto_start":
        _print_result(result["start"], as_json=False)
        print("")
        _print_result(result["auto"], as_json=False)
        return
    if kind == "tick":
        print(f"Action: {result['action']}")
        if result.get("reason"):
            print(f"Reason: {result['reason']}")
        if result.get("processed_child_task"):
            print(f"Processed child: {result['processed_child_task']}")
        if result.get("next_phase"):
            print(f"Next phase: {result['next_phase']}")
        if result.get("created_task_id"):
            print(f"Created task: {result['created_task_id']}")
        return
    if kind == "auto":
        print(f"Auto outcome: {result['outcome']}")
        print(f"Root:         {result['root_task_id']}")
        print(f"Board:        {result['board']}")
        print(f"Steps:        {result['steps']}")
        if result.get("reason"):
            print(f"Reason:       {result['reason']}")
        if result.get("actions"):
            last = result["actions"][-1]
            print(f"Last action:  {last.get('action')}")
            if last.get("created_task_id"):
                print(f"Created task: {last['created_task_id']}")
            if last.get("next_phase"):
                print(f"Next phase:   {last['next_phase']}")
        if result.get("notification_target"):
            target = result["notification_target"]
            print(
                "Notify:       "
                f"{target.get('platform')}:{target.get('chat_id')}"
                + (f":{target.get('thread_id')}" if target.get("thread_id") else "")
            )
        return
    if kind == "status":
        print(f"Root:    {result['root_task_id']} ({result['root_status']})")
        print(f"Issue:   {result.get('issue') or '(unknown)'}")
        print(f"Phase:   {result.get('current_phase') or '(unknown)'}")
        print(f"Child tasks: {len(result.get('children') or [])}")
        for child in result.get("children") or []:
            print(
                f"  - {child['id']} {child['status']:8s} "
                f"{child.get('phase') or '?'} v{child.get('iteration') or '?'} "
                f"-> {child.get('assignee') or '(unassigned)'} "
                f"[{child.get('execution_backend') or 'hermes_worker'}]"
            )
        if result.get("next_action"):
            print(f"Next:    {result['next_action']}")
        return
    if kind == "pr_review":
        print(f"PR review task: {result['task_id']}")
        print(f"Board:          {result['board']}")
        print(f"PR:             {result['pr_url']}")
        print(f"Branch:         {result.get('branch') or '(unknown)'}")
        print(f"Worktree:       {result.get('worktree') or '(unknown)'}")
        print(f"Worktree mode:  {result.get('worktree_source') or '(unknown)'}")
        return

    print(json.dumps(result, ensure_ascii=False, indent=2))


def odoo_hedge_workflow_command(args: argparse.Namespace) -> int:
    try:
        command = args.workflow_command
        if command == "start":
            result = workflow.start_workflow(
                issue=args.issue,
                board=args.board,
                repo=args.repo,
                worktree=args.worktree,
                base_branch=args.base_branch,
                branch=args.branch,
                topic=args.topic,
                create_worktree=not args.no_create_worktree,
                title=args.title,
                notify_platform=args.notify_platform,
                notify_chat_id=args.notify_chat_id,
                notify_thread_id=args.notify_thread_id,
                notify_user_id=args.notify_user_id,
                notifier_profile=args.notifier_profile,
            )
            if args.auto:
                auto_result = workflow.run_auto_workflow(
                    root_task_id=result["root_task_id"],
                    board=args.board,
                    poll_seconds=args.poll_seconds,
                    max_steps=args.max_steps,
                )
                result = {"kind": "auto_start", "start": result, "auto": auto_result}
            _print_result(result, as_json=args.json)
            if args.auto and result["auto"].get("outcome") == "timeout":
                return 1
            return 0
        if command == "tick":
            if args.auto:
                result = workflow.run_auto_workflow(
                    root_task_id=args.root_task_id,
                    board=args.board,
                    poll_seconds=args.poll_seconds,
                    max_steps=args.max_steps,
                    notify_platform=args.notify_platform,
                    notify_chat_id=args.notify_chat_id,
                    notify_thread_id=args.notify_thread_id,
                    notify_user_id=args.notify_user_id,
                    notifier_profile=args.notifier_profile,
                )
                _print_result(result, as_json=args.json)
                return 1 if result.get("outcome") == "timeout" else 0
            result = workflow.tick_workflow(
                root_task_id=args.root_task_id,
                board=args.board,
                apply=args.apply,
            )
            _print_result(result, as_json=args.json)
            if not args.apply and not args.plan and result.get("action") == "create_next_task":
                print("Use --apply to create this task.", file=sys.stderr)
            return 0
        if command == "status":
            result = workflow.workflow_status(args.root_task_id, board=args.board)
            _print_result(result, as_json=args.json)
            return 0
        if command == "pr-review":
            result = workflow.start_pr_review(
                " ".join(args.request or []).strip(),
                board=args.board,
                repo=args.repo,
                requester="cli",
            )
            _print_result(result, as_json=args.json)
            return 0
        if command == "codex-exec-worker":
            result = workflow.run_codex_exec_worker(args.task_id, board=args.board)
            _print_result(result, as_json=True)
            return 0
    except Exception as exc:
        print(f"odoo-hedge-workflow: {exc}", file=sys.stderr)
        return 1

    print(f"odoo-hedge-workflow: unknown command {getattr(args, 'workflow_command', None)!r}", file=sys.stderr)
    return 1
