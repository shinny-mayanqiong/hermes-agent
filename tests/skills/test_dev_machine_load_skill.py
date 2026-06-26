"""Tests for the bundled dev-machine-load skill."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO_ROOT / "skills" / "devops" / "dev-machine-load" / "SKILL.md"


def test_dev_machine_load_skill_routes_to_devload_json():
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert "hermes devload --json" in text
    assert "Do not use a `/devload` slash command" in text
    assert "Do not add or request a model tool" in text
