"""退出清理：REPL 与一次性执行都必须关掉 MCP 子进程和联网会话。

背景：`McpManager.disconnect_all()` 与 `close_online_tools()` 之前没有任何调用点，
正常退出时 aiohttp 会话常驻、Windows 下还会留下 node 子进程，并在解释器收尾时刷出
unclosed transport 警告。
"""

from __future__ import annotations

import asyncio

import agents.online_tools as online_tools
from agents import main as cli


class StubAgent:
    def __init__(self, *, fail_chat: bool = False):
        self.calls: list[str] = []
        self.fail_chat = fail_chat

    async def chat(self, prompt: str) -> None:
        self.calls.append("chat")
        if self.fail_chat:
            raise RuntimeError("model failed")

    async def drain_background_skill_tasks(self) -> None:
        self.calls.append("drain")

    async def close(self) -> None:
        self.calls.append("mcp_close")

    def set_confirm_fn(self, fn) -> None:
        pass

    def set_plan_approval_fn(self, fn) -> None:
        pass


def patch_online_close(monkeypatch, calls: list[str]) -> None:
    async def fake_close() -> None:
        calls.append("online_close")

    monkeypatch.setattr(online_tools, "close_online_tools", fake_close)


def test_one_shot_closes_mcp_and_online_tools(monkeypatch):
    agent = StubAgent()
    patch_online_close(monkeypatch, agent.calls)

    asyncio.run(cli.run_one_shot(agent, "hello"))

    assert agent.calls == ["chat", "drain", "mcp_close", "online_close"]


def test_one_shot_still_cleans_up_when_chat_fails(monkeypatch):
    agent = StubAgent(fail_chat=True)
    patch_online_close(monkeypatch, agent.calls)

    try:
        asyncio.run(cli.run_one_shot(agent, "hello"))
    except RuntimeError:
        pass
    else:
        raise AssertionError("chat 的异常应原样抛给调用方")

    assert agent.calls == ["chat", "drain", "mcp_close", "online_close"]


def test_repl_exit_closes_mcp_and_online_tools(monkeypatch):
    agent = StubAgent()
    patch_online_close(monkeypatch, agent.calls)

    async def fake_read() -> str:
        return "exit"

    monkeypatch.setattr(cli, "_read_user_line", fake_read)
    monkeypatch.setattr(cli.signal, "signal", lambda *args: None)
    for name in ("print_user_prompt", "print_welcome", "print_goodbye"):
        monkeypatch.setattr(cli, name, lambda *a, **k: None)

    asyncio.run(cli.run_repl(agent))

    assert agent.calls == ["drain", "mcp_close", "online_close"]
