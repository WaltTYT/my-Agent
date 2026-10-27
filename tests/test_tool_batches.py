"""OpenAI 分支一次返回多个工具调用时，每个调用只能执行一次。

背景：批处理块的构建与执行曾经写在 `for tc in tool_calls` 循环体内，第 k 个调用会把
前 k 个调用重新跑一遍，n 个调用放大成 n(n+1)/2 次执行，文件读取和命令执行被重复触发。
"""

from __future__ import annotations

import asyncio

import pytest

import agents.agent as agent_module
from agents.agent import Agent


def make_agent(executed: list[str]) -> Agent:
    """装配一个只跑 OpenAI 工具批处理分支的 Agent，不发真实请求。"""
    agent = Agent.__new__(Agent)
    agent.use_openai = True
    agent.is_sub_agent = True
    agent.permission_mode = "bypassPermissions"
    agent._plan_file_path = None
    agent._confirmed_paths = set()
    agent._openai_messages = [{"role": "system", "content": "BASE PROMPT"}]
    agent._pending_download_url = None
    agent._download_nudges = 0
    agent._aborted = False
    agent._context_cleared = False
    agent.current_turns = 0
    agent.total_input_tokens = 0
    agent.total_output_tokens = 0

    agent._normalize_plan_file_input = lambda name, inp: inp
    agent._emit_text = lambda text: None
    agent._run_compression_pipeline = lambda: None
    agent._check_budget = lambda: {"exceeded": False}
    agent._persist_large_result = lambda name, raw, inp: raw
    agent._looks_like_tool_failure = lambda name, raw, res: False
    agent._record_tool_outcome = lambda name, success: None
    agent._refresh_runtime_system_prompt = lambda: None

    async def run(name: str, inp: dict) -> str:
        executed.append(name)
        return f"result of {name}"

    agent._execute_tool_call = run

    async def noop() -> None:
        return None

    agent._check_and_compact = noop
    return agent


def tool_calls_response(names: list[str]) -> dict:
    calls = [
        {
            "id": f"call_{i}",
            "type": "function",
            "function": {"name": name, "arguments": "{}"},
        }
        for i, name in enumerate(names)
    ]
    return {
        "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": calls}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


def stop_response() -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": "done", "tool_calls": None}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


@pytest.fixture
def silent(monkeypatch):
    monkeypatch.setattr(agent_module, "check_permission", lambda *a, **k: {"action": "allow"})
    monkeypatch.setattr(agent_module, "print_tool_call", lambda *a, **k: None)
    monkeypatch.setattr(agent_module, "print_tool_result", lambda *a, **k: None)
    monkeypatch.setattr(agent_module, "print_info", lambda *a, **k: None)
    monkeypatch.setattr(agent_module, "print_cost", lambda *a, **k: None)


def test_multiple_readonly_calls_execute_once_each(silent):
    executed: list[str] = []
    agent = make_agent(executed)
    responses = [tool_calls_response(["read_file", "list_files", "grep_search"]), stop_response()]

    async def fake_stream() -> dict:
        return responses.pop(0)

    agent._call_openai_stream = fake_stream
    asyncio.run(agent._chat_openai("读三个文件"))

    assert executed == ["read_file", "list_files", "grep_search"]
    tool_messages = [m for m in agent._openai_messages if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["call_0", "call_1", "call_2"]


def test_denied_call_is_not_replayed_before_later_calls(silent, monkeypatch):
    executed: list[str] = []
    agent = make_agent(executed)
    agent.permission_mode = "default"

    def permission(name, inp, mode, plan_file_path):
        if name == "read_file":
            return {"action": "deny", "message": "blocked"}
        return {"action": "allow"}

    monkeypatch.setattr(agent_module, "check_permission", permission)
    responses = [tool_calls_response(["read_file", "grep_search"]), stop_response()]

    async def fake_stream() -> dict:
        return responses.pop(0)

    agent._call_openai_stream = fake_stream
    asyncio.run(agent._chat_openai("先被拒再读"))

    assert executed == ["grep_search"]
    tool_messages = [m for m in agent._openai_messages if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["call_0", "call_1"]
    assert tool_messages[0]["content"] == "Action denied: blocked"
