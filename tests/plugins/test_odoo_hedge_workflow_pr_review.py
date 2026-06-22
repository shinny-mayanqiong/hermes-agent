"""Tests for the Odoo Hedge PR review workflow entrypoint."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from hermes_cli import kanban_db as kb


PLUGIN_DIR = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "odoo-hedge-workflow"
)


def _load_plugin_package():
    package_name = "test_odoo_hedge_workflow_plugin"
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
def pr_review(plugin):
    return importlib.import_module(plugin.__name__ + ".pr_review")


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db(board="odoo-hedge-dev")
    return home


def test_parse_pr_review_number(workflow):
    ref, focus = workflow.parse_pr_review_request(
        "42 security and data integrity",
        default_repo_slug="shinnytech/odoo-hedge",
    )

    assert ref.owner == "shinnytech"
    assert ref.repo_name == "odoo-hedge"
    assert ref.number == 42
    assert ref.url == "https://github.com/shinnytech/odoo-hedge/pull/42"
    assert focus == "security and data integrity"


def test_parse_pr_review_url_and_slack_wrapping(workflow):
    ref, focus = workflow.parse_pr_review_request(
        "<https://github.com/shinnytech/odoo-hedge/pull/77|PR 77> UI impact",
    )

    assert ref.repo_slug == "shinnytech/odoo-hedge"
    assert ref.number == 77
    assert focus == "UI impact"


def test_parse_pr_review_rejects_invalid_input(workflow):
    with pytest.raises(ValueError, match="Usage: /pr-review"):
        workflow.parse_pr_review_request("not-a-pr")


def test_start_pr_review_creates_review_only_kanban_task(
    workflow,
    kanban_home,
    tmp_path,
    monkeypatch,
):
    repo = tmp_path / "odoo-hedge"
    worktree = tmp_path / "review-worktree"
    repo.mkdir()
    worktree.mkdir()

    monkeypatch.setattr(workflow, "_github_repo_slug", lambda _repo: "shinnytech/odoo-hedge")
    monkeypatch.setattr(
        workflow,
        "_load_pr_metadata",
        lambda _pr: {
            "number": 42,
            "url": "https://github.com/shinnytech/odoo-hedge/pull/42",
            "title": "Fix hedge valuation",
            "body": "PR body",
            "author": {"login": "alice"},
            "baseRefName": "master",
            "headRefName": "feature/valuation",
            "headRefOid": "abcdef1234567890",
            "isDraft": False,
            "mergeStateStatus": "CLEAN",
            "reviewDecision": "",
            "statusCheckRollup": [],
        },
    )
    monkeypatch.setattr(
        workflow,
        "_prepare_pr_review_worktree",
        lambda **_kw: workflow.WorktreeResolution(
            repo=str(repo),
            worktree=str(worktree),
            branch="codex/pr-review-42-abcdef123456",
            topic="pr-review-42",
            topic_source="pr_review",
            source="created",
            resources={"odoo_db_name": "rd_demo_pr_review"},
        ),
    )

    result = workflow.start_pr_review(
        "42 focus on accounting edge cases",
        board="odoo-hedge-dev",
        repo=str(repo),
        requester="Alice (U123)",
    )

    assert result["kind"] == "pr_review"
    assert result["task_id"].startswith("t_")
    with kb.connect_closing(board="odoo-hedge-dev") as conn:
        task = kb.get_task(conn, result["task_id"])
    assert task is not None
    meta = workflow._task_meta(task)
    assert meta["entry"] == workflow.PR_REVIEW_ENTRY
    assert meta["review_only"] is True
    assert meta["phase"] == "local_code_review"
    assert meta["execution_backend"] == "codex_exec"
    assert meta["pr"]["number"] == 42
    assert meta["review_focus"] == "focus on accounting edge cases"
    assert task.assignee == workflow.CODE_REVIEWER
    assert task.workspace_path == str(worktree)
    with kb.connect_closing(board="odoo-hedge-dev") as conn:
        comments = kb.list_comments(conn, result["task_id"])
    assert any("pr: shinnytech/odoo-hedge#42" in comment.body for comment in comments)
    assert all("https://github.com/shinnytech/odoo-hedge/pull/42" not in comment.body for comment in comments)


def test_codex_exec_prompt_includes_pr_review_contract(workflow):
    task = SimpleNamespace(id="t_123")
    meta = {
        "entry": workflow.PR_REVIEW_ENTRY,
        "review_only": True,
        "phase": "local_code_review",
        "issue": None,
        "worktree": "/tmp/review",
        "branch": "codex/pr-review-42-abcdef",
        "review_focus": "security",
        "pr": {
            "number": 42,
            "url": "https://github.com/shinnytech/odoo-hedge/pull/42",
            "title": "Fix hedge valuation",
            "baseRefName": "master",
            "headRefName": "feature/valuation",
            "headRefOid": "abcdef1234567890",
        },
        "workflow_id": "pr-review-42-abcdef123456",
    }

    prompt = workflow._codex_exec_prompt(task, meta)

    assert "PR review context" in prompt
    assert "https://github.com/shinnytech/odoo-hedge/pull/42" in prompt
    assert "只做 review，不修改代码、不提交、不 push、不创建 PR" in prompt
    assert "pr_comment_url" in prompt


class FakeSlackClient:
    def __init__(self):
        self.messages = []
        self._counter = 100

    async def chat_postMessage(self, **kwargs):
        self._counter += 1
        ts = f"{self._counter}.000"
        self.messages.append({**kwargs, "ts": ts})
        return {"ok": True, "ts": ts}


class FakeSlackAdapter:
    def __init__(self):
        self.client = FakeSlackClient()
        self._bot_message_ts = set()
        self._mentioned_threads = set()
        self.pop_slash_context_count = 0

    def _get_client(self, chat_id):
        del chat_id
        return self.client

    def _pop_slash_context(self, chat_id):
        del chat_id
        self.pop_slash_context_count += 1
        return {"response_url": "https://slack.example/response"}

    def format_message(self, content):
        return content


def _source(*, thread_id=None):
    return SimpleNamespace(
        platform=Platform.SLACK,
        chat_id="C123",
        user_id="U123",
        user_name="Alice",
        thread_id=thread_id,
        guild_id="T123",
        chat_type="group",
        chat_name="general",
    )


def _event(text, *, thread_id=None):
    return SimpleNamespace(
        text=text,
        source=_source(thread_id=thread_id),
        raw_message={"team_id": "T123", "text": text},
    )


@pytest.mark.asyncio
async def test_slack_pr_review_creates_task_thread_and_subscription(pr_review, monkeypatch):
    adapter = FakeSlackAdapter()
    gateway = SimpleNamespace(
        adapters={Platform.SLACK: adapter},
        _active_profile_name=lambda: "default",
    )
    subscriptions = []

    def fake_start(args, *, board, repo, requester):
        assert args == "42 security"
        assert board == "odoo-hedge-dev"
        assert repo == "/home/user/Repos/odoo-hedge"
        assert requester == "Alice (U123)"
        return {
            "task_id": "t_abc123",
            "pr_url": "https://github.com/shinnytech/odoo-hedge/pull/42",
            "worktree": "/tmp/review",
        }

    def fake_subscribe(**kwargs):
        subscriptions.append(kwargs)

    monkeypatch.setattr(pr_review.workflow, "start_pr_review", fake_start)
    monkeypatch.setattr(pr_review, "_subscribe_to_task", fake_subscribe)

    await pr_review._handle_slack_pr_review_event(
        _event("/pr-review 42 security"),
        gateway,
        "42 security",
    )

    assert len(adapter.client.messages) == 2
    root, reply = adapter.client.messages
    assert "Odoo Hedge PR review requested" in root["text"]
    assert reply["thread_ts"] == root["ts"]
    assert "Kanban task: `t_abc123`" in reply["text"]
    assert subscriptions[0]["task_id"] == "t_abc123"
    assert subscriptions[0]["thread_ts"] == root["ts"]
    assert adapter.pop_slash_context_count == 1


def test_pre_gateway_dispatch_does_not_bypass_auth(pr_review):
    result = pr_review.pre_gateway_dispatch(
        _event("/pr-review 42"),
        SimpleNamespace(
            adapters={},
            _is_user_authorized=lambda _source: False,
        ),
    )

    assert result == {"action": "allow"}


def test_register_exposes_pr_review_command_and_hook(plugin):
    class FakeCtx:
        def __init__(self):
            self.commands = []
            self.hooks = []

        def register_cli_command(self, *args, **kwargs):
            pass

        def register_command(self, *args, **kwargs):
            self.commands.append((args, kwargs))

        def register_hook(self, *args, **kwargs):
            self.hooks.append((args, kwargs))

    ctx = FakeCtx()
    plugin.register(ctx)

    assert any(args[0] == "pr-review" for args, _kwargs in ctx.commands)
    hook_names = [args[0] for args, _kwargs in ctx.hooks]
    assert "pre_gateway_dispatch" in hook_names
    assert "kanban_spawn_override" in hook_names
