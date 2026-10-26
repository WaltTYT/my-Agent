"""计划模式的工具可见性与退出流程。

这些用例对应一组真实踩过的坑：计划模式下仍然把写类工具发给模型，模型于是调
save_memory 去"保存计划"，被权限拒绝后转而向用户宣称已经保存成功；同时
exit_plan_mode 漏了 await 直接抛错，并且批准后不会恢复 permission_mode。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import agents.agent as agent_module
from agents import tools
from agents.agent import Agent


def make_agent() -> Agent:
    """装配一个只够跑计划模式状态流转的 Agent，不创建模型客户端。

    直接调 Agent(...) 会构造 anthropic/openai 客户端，而这些用例一次都不会发请求，
    所以绕过 __init__ 手工给出被测代码真正读写的字段。
    """
    agent = Agent.__new__(Agent)
    agent.model = "glm-4"
    agent.use_openai = False
    agent.is_sub_agent = False
    agent.tools = tools.tool_definitions
    agent.session_id = "test0001"
    agent.permission_mode = "default"
    agent._pre_plan_mode = None
    agent._plan_file_path = None
    agent._plan_approval_fn = None
    agent._base_system_prompt = "BASE PROMPT"
    agent._system_prompt = "BASE PROMPT"
    agent._anthropic_messages = []
    agent._openai_messages = []
    agent.last_input_token_count = 0
    agent._fold_last_time = 0.0
    agent._fold_count = 0
    return agent


def in_plan_mode(agent: Agent) -> Agent:
    """复现 toggle_plan_mode 进入计划模式后的状态。"""
    agent.permission_mode = "plan"
    agent._pre_plan_mode = "default"
    agent._plan_file_path = agent._generate_plan_file_path()
    agent._system_prompt = agent._base_system_prompt + agent._build_plan_mode_prompt()
    return agent


def write_plan(agent: Agent, body: str) -> Path:
    path = Path(agent._plan_file_path)
    path.write_text(body, encoding="utf-8")
    return path


def test_plan_mode_hides_tools_that_would_only_be_denied():
    plan_tools = {t["name"] for t in tools.get_plan_mode_tool_definitions()}
    normal_tools = {t["name"] for t in tools.get_active_tool_definitions()}

    # 这些工具在计划模式下必然被 check_permission 拒绝，不该出现在请求里。
    for blocked in ("save_memory", "delete_memory", "run_shell", "skill", "skill_create", "skill_evolve"):
        assert blocked in normal_tools, f"{blocked} 普通模式应该可见"
        assert blocked not in plan_tools, f"{blocked} 计划模式不该暴露"

    # 只读工具和计划文件写入必须保留，否则没法规划。
    assert {"read_file", "grep_search", "list_files"} <= plan_tools
    assert {"write_file", "edit_file", "exit_plan_mode"} <= plan_tools

    # 联网工具应该在计划模式下可用
    assert {"search_web", "get_web_content"} <= plan_tools


def test_request_tool_definitions_follows_current_mode():
    agent = make_agent()
    assert "save_memory" in {t["name"] for t in agent._request_tool_definitions()}

    in_plan_mode(agent)
    assert "save_memory" not in {t["name"] for t in agent._request_tool_definitions()}


def test_plan_mode_subagent_returns_analysis_without_owning_plan_file(monkeypatch):
    agent = make_agent()
    agent.permission_mode = "plan"
    agent._plan_file_path = r"D:\project\memory\plans\parent-plan.md"
    agent.use_openai = False
    agent._openai_client = None
    agent.total_input_tokens = 0
    agent.total_output_tokens = 0
    captured = {}

    class StubSubAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run_once(self, prompt: str) -> dict:
            return {"tokens": {"input": 3, "output": 2}, "text": "分析结果"}

    monkeypatch.setattr(agent_module, "Agent", StubSubAgent)
    result = asyncio.run(agent._execute_agent_tool({
        "type": "general",
        "description": "检查实现方案",
        "prompt": "分析相关模块",
    }))

    assert result == "分析结果"
    assert captured["permission_mode"] == "plan"
    assert captured["is_sub_agent"] is True
    assert {tool["name"] for tool in captured["custom_tools"]} == {
        "read_file", "list_files", "grep_search"
    }
    assert "Do not edit files or create a plan file" in captured["custom_system_prompt"]
    assert agent._plan_file_path.endswith("parent-plan.md")


def test_plan_mode_subagent_tool_request_is_read_only():
    agent = make_agent()
    agent.permission_mode = "plan"
    agent.is_sub_agent = True

    assert {tool["name"] for tool in agent._request_tool_definitions()} == {
        "read_file", "list_files", "grep_search"
    }


def test_plan_mode_deny_message_points_at_the_plan_file():
    perm = tools.check_permission(
        "save_memory", {"name": "x"}, "plan", r"D:\proj\memory\plans\plan-ab.md"
    )
    assert perm["action"] == "deny"
    message = perm["message"]
    assert "write_file" in message
    assert "plan-ab.md" in message
    # 必须明确否定"已保存"，否则模型会直接对用户宣称成功。
    assert "不要向用户声称已保存" in message


def test_exit_plan_mode_awaits_approval_callback():
    # 回调是 async 的，漏 await 会让 result 变成协程对象并抛 AttributeError。
    agent = in_plan_mode(make_agent())
    write_plan(agent, "# 计划\n第一步\n")

    async def approve(plan_content: str) -> dict:
        return {"choice": "execute"}

    agent.set_plan_approval_fn(approve)
    result = asyncio.run(agent._execute_plan_mode_tool("exit_plan_mode"))
    assert "User approved the plan" in result


def test_exit_plan_mode_passes_file_content_not_path():
    agent = in_plan_mode(make_agent())
    write_plan(agent, "# 我的计划\n第一步：做 X\n")
    seen: dict[str, str] = {}

    async def approve(plan_content: str) -> dict:
        seen["content"] = plan_content
        return {"choice": "execute"}

    agent.set_plan_approval_fn(approve)
    result = asyncio.run(agent._execute_plan_mode_tool("exit_plan_mode"))

    assert seen["content"].startswith("# 我的计划")
    # 审批面板和回给模型的正文都必须是内容，不能只有路径。
    assert "第一步：做 X" in result


def test_exit_plan_mode_reports_missing_file():
    agent = in_plan_mode(make_agent())

    async def approve(plan_content: str) -> dict:
        return {"choice": "execute"}

    agent.set_plan_approval_fn(approve)
    result = asyncio.run(agent._execute_plan_mode_tool("exit_plan_mode"))
    assert "(No plan file found)" in result


@pytest.mark.parametrize("choice,expected_mode", [
    ("execute", "acceptEdits"),
    ("clear-and-execute", "acceptEdits"),
    ("manual-execute", "default"),
])
def test_exit_plan_mode_restores_permission_mode(choice: str, expected_mode: str):
    agent = in_plan_mode(make_agent())
    write_plan(agent, "# 计划\n")

    async def approve(plan_content: str) -> dict:
        return {"choice": choice}

    agent.set_plan_approval_fn(approve)
    asyncio.run(agent._execute_plan_mode_tool("exit_plan_mode"))

    # 批准之后必须离开只读，否则后续所有写操作仍会被拒。
    assert agent.permission_mode == expected_mode
    assert agent._plan_file_path is None
    assert agent._system_prompt == agent._base_system_prompt


def test_keep_planning_stays_in_plan_mode():
    agent = in_plan_mode(make_agent())
    write_plan(agent, "# 第一版\n")

    async def reject(plan_content: str) -> dict:
        return {"choice": "keep-planning", "feedback": "缺少验证步骤"}

    agent.set_plan_approval_fn(reject)
    result = asyncio.run(agent._execute_plan_mode_tool("exit_plan_mode"))

    assert "缺少验证步骤" in result
    assert agent.permission_mode == "plan"
