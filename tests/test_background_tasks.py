"""后台技能判定使用本轮快照，REPL 读输入不再阻塞事件循环。

背景：`_run_skill_usage_tracking` 曾经在真正执行时才读 `self._last_retrieved_skill_hits`，
而同步 `input()` 让事件循环停摆，后台任务拖到下一轮才跑，于是上一轮的判定读到了下一轮
的命中列表。现在命中列表在调度时拷贝传入，输入读取放到守护线程里等待。
"""

from __future__ import annotations

import asyncio
import threading

import pytest

import agents.online_skill_evolution as online_skill_evolution
import agents.skills as skills_module
from agents import main as cli
from agents.agent import Agent


def make_agent() -> Agent:
    agent = Agent.__new__(Agent)
    agent.permission_mode = "default"
    agent.is_sub_agent = False
    agent._last_retrieved_skill_hits = [{"name": "next_round_skill"}]
    return agent


def test_usage_tracking_uses_the_snapshot_from_its_own_turn(monkeypatch):
    captured: dict[str, list[dict]] = {}

    async def fake_judge(*, hits, user_message, assistant_text, side_query=None):
        captured["hits"] = hits
        return []

    monkeypatch.setattr(online_skill_evolution, "judge_retrieved_skill_usage", fake_judge)
    monkeypatch.setattr(skills_module, "record_usage_judgments", lambda judgments: {"pruned": []})

    agent = make_agent()
    agent._build_side_query = lambda max_tokens=700: None

    asyncio.run(
        agent._run_skill_usage_tracking("上一轮问题", "上一轮回答", [{"name": "snapshot_skill"}])
    )

    assert captured["hits"] == [{"name": "snapshot_skill"}]


def test_usage_tracking_still_skips_without_hits(monkeypatch):
    called = []
    monkeypatch.setattr(
        online_skill_evolution,
        "judge_retrieved_skill_usage",
        lambda **kwargs: called.append(kwargs),
    )
    agent = make_agent()

    asyncio.run(agent._run_skill_usage_tracking("问题", "回答", []))

    assert called == []


def test_read_user_line_returns_the_typed_text(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda: "hello")
    assert asyncio.run(cli._read_user_line()) == "hello"


def test_read_user_line_keeps_the_loop_running_while_waiting(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def fake_input() -> str:
        started.set()
        release.wait(5)
        return "typed while waiting"

    monkeypatch.setattr("builtins.input", fake_input)

    async def scenario() -> str:
        task = asyncio.create_task(cli._read_user_line())
        while not started.is_set():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.02)
        assert not task.done()
        release.set()
        return await asyncio.wait_for(task, 5)

    assert asyncio.run(scenario()) == "typed while waiting"


def test_read_user_line_propagates_eof(monkeypatch):
    def raise_eof() -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)

    with pytest.raises(EOFError):
        asyncio.run(cli._read_user_line())


def test_repl_reads_input_through_the_async_helper(monkeypatch):
    class StubAgent:
        def set_confirm_fn(self, fn):
            pass

        def set_plan_approval_fn(self, fn):
            pass

        async def drain_background_skill_tasks(self):
            pass

    async def fake_read() -> str:
        return "exit"

    monkeypatch.setattr(cli, "_read_user_line", fake_read)
    monkeypatch.setattr(cli.signal, "signal", lambda *args: None)
    for name in ("print_user_prompt", "print_welcome", "print_goodbye"):
        monkeypatch.setattr(cli, name, lambda *a, **k: None)

    def boom() -> str:
        raise AssertionError("REPL 主循环不能再用阻塞 input()")

    monkeypatch.setattr("builtins.input", boom)
    asyncio.run(cli.run_repl(StubAgent()))
