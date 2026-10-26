import json
from pathlib import Path

import pytest

from agents.tools import check_permission, is_dangerous, reset_permission_cache


def test_read_tools_always_allowed_even_in_plan_mode():
    for tool in ("read_file", "list_files", "grep_search"):
        assert check_permission(tool, {"file_path": "x.py"}, mode="plan")["action"] == "allow"


def test_plan_mode_blocks_writes_and_shell():
    assert check_permission("write_file", {"file_path": "a.py"}, mode="plan")["action"] == "deny"
    assert check_permission("edit_file", {"file_path": "a.py"}, mode="plan")["action"] == "deny"
    assert check_permission("run_shell", {"command": "ls"}, mode="plan")["action"] == "deny"


def test_plan_mode_allows_writing_the_plan_file_only():
    plan = "/tmp/plan.md"
    assert check_permission("write_file", {"file_path": plan}, mode="plan", plan_file_path=plan)["action"] == "allow"
    assert check_permission("write_file", {"file_path": "other.md"}, mode="plan", plan_file_path=plan)["action"] == "deny"


@pytest.mark.parametrize(
    "command",
    ["rm -rf build", "sudo apt install x", "git push origin main", "git reset --hard", "kill 123", "echo x > /dev/sda"],
)
def test_dangerous_commands_detected(command):
    assert is_dangerous(command)
    assert check_permission("run_shell", {"command": command})["action"] == "confirm"


@pytest.mark.parametrize("command", ["ls -la", "git status", "python -m pytest", "grep -rn foo ."])
def test_safe_commands_pass(command):
    assert not is_dangerous(command)
    assert check_permission("run_shell", {"command": command})["action"] == "allow"


def test_dont_ask_mode_denies_instead_of_confirming():
    assert check_permission("run_shell", {"command": "rm a.txt"}, mode="dontAsk")["action"] == "deny"


def test_bypass_mode_allows_everything():
    assert check_permission("run_shell", {"command": "rm -rf /"}, mode="bypassPermissions")["action"] == "allow"


def test_creating_new_file_needs_confirmation(isolated_env):
    assert check_permission("write_file", {"file_path": "new.txt"})["action"] == "confirm"
    (isolated_env / "exists.txt").write_text("hi")
    assert check_permission("write_file", {"file_path": "exists.txt"})["action"] == "allow"


def test_accept_edits_mode_allows_new_files():
    assert check_permission("write_file", {"file_path": "new.txt"}, mode="acceptEdits")["action"] == "allow"


def test_project_rules_deny_beats_allow(isolated_env):
    settings = {"permissions": {"allow": ["run_shell(git *)"], "deny": ["run_shell(git push*)"]}}
    (isolated_env / ".lily").mkdir()
    (isolated_env / ".lily" / "settings.json").write_text(json.dumps(settings))
    reset_permission_cache()
    assert check_permission("run_shell", {"command": "git push origin main"})["action"] == "deny"
    assert check_permission("run_shell", {"command": "git status"})["action"] == "allow"
    # deny 规则在 plan 模式之前生效
    assert check_permission("run_shell", {"command": "git push"}, mode="plan")["action"] == "deny"
