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
    agent.model = "glm-4"
    agent.total_input_tokens = 0
    agent.total_output_tokens = 0
    agent.current_turns = 0
    agent.max_cost_usd = None
    agent.max_turns = None
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


def _make_gate_agent(mode: str) -> Agent:
    agent = Agent.__new__(Agent)
    agent.permission_mode = mode
    agent._last_retrieved_skill_hits = []
    agent.model = "glm-4"
    agent.total_input_tokens = 0
    agent.total_output_tokens = 0
    agent.current_turns = 0
    agent.max_cost_usd = None
    agent.max_turns = None
    return agent


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


def test_background_evolution_skipped_when_write_would_be_denied(monkeypatch):
    calls: list[dict] = []

    async def fake_ingest(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "action": "none"}

    monkeypatch.setenv("LILY_AUTO_SKILL_EVOLUTION", "1")
    monkeypatch.setattr(online_skill_evolution, "online_ingest", fake_ingest)

    agent = _make_gate_agent("default")
    agent._build_side_query = lambda **k: (lambda s, u: "")
    window = {"messages": [{"role": "user", "content": "q"}]}

    asyncio.run(agent._run_online_skill_evolution(window))
    assert calls == []

    asyncio.run(agent._run_online_skill_evolution(window, interactive_confirm=True))
    assert len(calls) == 1
    assert calls[0]["confirm_write"] == agent._confirm_online_skill_write

    agent.permission_mode = "bypassPermissions"
    asyncio.run(agent._run_online_skill_evolution(window))
    assert len(calls) == 2
    assert calls[1]["confirm_write"] == agent._confirm_background_online_skill_write


def test_usage_tracking_skipped_when_side_query_budget_blocked(monkeypatch):
    called: list[dict] = []

    async def fake_judge(**kwargs):
        called.append(kwargs)
        return []

    monkeypatch.setenv("LILY_AUTO_SKILL_EVOLUTION", "1")
    monkeypatch.delenv("LILY_SIDE_QUERY_BUDGET", raising=False)
    monkeypatch.setattr(online_skill_evolution, "judge_retrieved_skill_usage", fake_judge)

    agent = _make_gate_agent("default")
    agent.total_input_tokens = 2_000_000
    agent.max_cost_usd = 0.01

    asyncio.run(agent._run_skill_usage_tracking("q", "a", [{"name": "demo"}]))
    assert called == []


def test_judge_ignores_unusable_side_query_output():
    async def side_empty(system: str, user: str) -> str:
        return ""

    async def side_garbage(system: str, user: str) -> str:
        return "not json at all"

    async def side_empty_list(system: str, user: str) -> str:
        return '{"judgments": []}'

    hits = [{"name": "demo", "source": "project", "score": 0.5}]

    assert asyncio.run(online_skill_evolution.judge_retrieved_skill_usage(
        hits=hits, user_message="q", assistant_text="a", side_query=side_empty)) == []
    assert asyncio.run(online_skill_evolution.judge_retrieved_skill_usage(
        hits=hits, user_message="q", assistant_text="a", side_query=side_garbage)) == []

    rows = asyncio.run(online_skill_evolution.judge_retrieved_skill_usage(
        hits=hits, user_message="q", assistant_text="a", side_query=side_empty_list))
    assert len(rows) == 1 and rows[0]["used"] is False
