"""Tests for the Odoo Hedge workflow auto controller."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli import kanban_db as kb


PLUGIN_DIR = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "odoo-hedge-workflow"
)


def _load_plugin_package():
    package_name = "test_odoo_hedge_workflow_auto_plugin"
    for name in list(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        package_name,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def plugin():
    return _load_plugin_package()


@pytest.fixture
def workflow(plugin):
    return importlib.import_module(plugin.__name__ + ".workflow")


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db(board="odoo-hedge-dev")
    return home


@pytest.fixture
def fake_worktree(workflow, tmp_path, monkeypatch):
    repo = tmp_path / "odoo-hedge"
    worktree = tmp_path / "issue-955-worktree"
    repo.mkdir()
    worktree.mkdir()

    def _resolve_worktree(**_kwargs):
        return workflow.WorktreeResolution(
            repo=str(repo),
            worktree=str(worktree),
            branch="codex/issue-955-soft-delete-account",
            topic="soft-delete-account",
            topic_source="test",
            source="existing",
            resources={"odoo_db_name": "rd_demo_issue_955"},
        )

    monkeypatch.setattr(workflow, "_resolve_worktree", _resolve_worktree)
    return worktree


def _start(workflow, fake_worktree, **kwargs):
    del fake_worktree
    return workflow.start_workflow(
        issue=955,
        board="odoo-hedge-dev",
        repo="/unused/repo",
        worktree=None,
        base_branch="master",
        **kwargs,
    )


def _complete(task_id: str, result: dict) -> None:
    with kb.connect_closing(board="odoo-hedge-dev") as conn:
        assert kb.complete_task(
            conn,
            task_id,
            result=json.dumps(result, ensure_ascii=False),
            summary=f"{result.get('phase')} done",
            metadata=result,
        )


def test_spec_blueprint_review_output_contract_requires_next_action(workflow):
    example = workflow._phase_output_example(
        {"workflow_id": "issue-955"},
        "spec_blueprint_review",
        1,
    )

    assert example["approved"] is True
    assert example["review_decision"] == "approved|changes_requested"
    assert (
        example["workflow_next_action"]
        == "proceed_to_implementation|return_to_spec_freeze|return_to_blueprint_prompts"
    )
    assert example["next_recommended_phase"] == "implementation|spec_freeze|blueprint_prompts"


def _phase_child(phase: str, result: dict, *, iteration: int = 1):
    body = json.dumps(
        {
            "workflow_type": "odoo_hedge_dynamic_delivery_v1",
            "workflow_id": "issue-955",
            "phase": phase,
            "iteration": iteration,
        },
        ensure_ascii=False,
    )
    return SimpleNamespace(body=body, result=json.dumps(result, ensure_ascii=False))


def test_spec_blueprint_review_missing_next_action_does_not_advance(workflow):
    child = _phase_child(
        "spec_blueprint_review",
        {
            "workflow_id": "issue-955",
            "phase": "spec_blueprint_review",
            "iteration": 1,
            "status": "done",
            "approved": True,
            "review_decision": "approved",
            "next_recommended_phase": "implementation",
        },
    )

    next_phase, iteration, reason = workflow._next_from_child(child)

    assert next_phase is None
    assert iteration == 1
    assert reason == "spec_blueprint_review result missing workflow_next_action"


def test_spec_blueprint_review_full_contract_advances_to_implementation(workflow):
    child = _phase_child(
        "spec_blueprint_review",
        {
            "workflow_id": "issue-955",
            "phase": "spec_blueprint_review",
            "iteration": 1,
            "status": "done",
            "approved": True,
            "review_decision": "approved",
            "workflow_next_action": "proceed_to_implementation",
            "next_recommended_phase": "implementation",
            "review_summary": "spec and blueprint are implementable",
            "ui_ux_impact": "no visible UI change",
            "clarity_findings": [],
            "step_findings": [],
            "blocking_findings": [],
            "non_blocking_findings": [],
        },
    )

    assert workflow._next_from_child(child) == (
        "implementation",
        1,
        "spec/blueprint review approved",
    )


def test_start_notification_subscribes_root_thread_without_replaying_history(
    workflow,
    kanban_home,
    fake_worktree,
):
    result = _start(
        workflow,
        fake_worktree,
        notify_platform="slack",
        notify_chat_id="C123",
        notify_thread_id="1719000000.000100",
        notify_user_id="U123",
    )

    with kb.connect_closing(board="odoo-hedge-dev") as conn:
        subs = kb.list_notify_subs(conn, result["root_task_id"])
        assert len(subs) == 1
        assert subs[0]["platform"] == "slack"
        assert subs[0]["chat_id"] == "C123"
        assert subs[0]["thread_id"] == "1719000000.000100"
        _, _, events = kb.claim_unseen_events_for_sub(
            conn,
            task_id=result["root_task_id"],
            platform="slack",
            chat_id="C123",
            thread_id="1719000000.000100",
            kinds=("blocked", "completed"),
        )

    assert events == []
    assert result["notification_target"]["thread_id"] == "1719000000.000100"


def test_auto_pauses_root_and_notifies_when_active_child_is_blocked(
    workflow,
    kanban_home,
    fake_worktree,
):
    result = _start(
        workflow,
        fake_worktree,
        notify_platform="slack",
        notify_chat_id="C123",
        notify_thread_id="1719000000.000100",
    )
    root_id = result["root_task_id"]
    child_id = result["child_task_id"]

    with kb.connect_closing(board="odoo-hedge-dev") as conn:
        assert kb.block_task(conn, child_id, reason="needs user decision")

    auto = workflow.run_auto_workflow(
        root_task_id=root_id,
        board="odoo-hedge-dev",
        poll_seconds=0,
        max_steps=1,
    )

    assert auto["outcome"] == "paused"
    assert auto["reason"] == f"active child {child_id} is blocked"
    with kb.connect_closing(board="odoo-hedge-dev") as conn:
        root = kb.get_task(conn, root_id)
        assert root is not None
        assert root.status == "blocked"
        _, _, events = kb.claim_unseen_events_for_sub(
            conn,
            task_id=root_id,
            platform="slack",
            chat_id="C123",
            thread_id="1719000000.000100",
            kinds=("blocked", "completed"),
        )

    assert [event.kind for event in events] == ["blocked"]
    assert events[0].payload["reason"] == f"active child {child_id} is blocked"


def test_auto_completes_root_after_closeout_completed(
    workflow,
    kanban_home,
    fake_worktree,
):
    result = _start(workflow, fake_worktree)
    root_id = result["root_task_id"]

    with kb.connect_closing(board="odoo-hedge-dev") as conn:
        root = workflow._load_task(conn, root_id)
        root_meta = workflow._validate_root(root)
        closeout_id = workflow._create_child_task(
            conn,
            root=root,
            root_meta=root_meta,
            phase="closeout_sync",
            iteration=1,
            parent_task_id=None,
            board="odoo-hedge-dev",
        )
    _complete(
        closeout_id,
        {
            "workflow_id": "issue-955",
            "phase": "closeout_sync",
            "iteration": 1,
            "status": "done",
            "closeout_synced": True,
            "pr_merged": True,
            "issue_closed": True,
            "project_status": "Done",
            "closeout_summary": "merged and synced",
            "artifacts": [],
            "blockers": [],
        },
    )

    auto = workflow.run_auto_workflow(
        root_task_id=root_id,
        board="odoo-hedge-dev",
        poll_seconds=0,
        max_steps=1,
    )

    assert auto["outcome"] == "completed"
    with kb.connect_closing(board="odoo-hedge-dev") as conn:
        root = kb.get_task(conn, root_id)
    assert root is not None
    assert root.status == "done"
    assert "Workflow completed: closeout completed" in (root.result or "")
