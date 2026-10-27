"""静默异常分级后，关键失败路径必须留下可排障的日志。"""

from __future__ import annotations

import asyncio
import logging

import pytest

import agents.agent as agent_module
from agents.agent import Agent


def _make_agent(**attrs) -> Agent:
    agent = Agent.__new__(Agent)
    for key, value in attrs.items():
        setattr(agent, key, value)
    return agent


def test_auto_save_logs_warning_when_save_fails(monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(agent_module, "save_session", boom)
    agent = _make_agent(
        session_id="s1",
        model="glm-4",
        session_start_time="now",
        use_openai=False,
        _anthropic_messages=[],
        _openai_messages=[],
        _folded_session_memories=[],
    )

    with caplog.at_level(logging.WARNING):
        agent._auto_save()

    assert "session auto-save failed" in caplog.text


class _RaisingTask:
    def result(self):
        raise RuntimeError("recall broken")


class _SettledPrefetch:
    settled = True
    consumed = False
    task = _RaisingTask()


class _StopLoop(Exception):
    pass


def _wire_chat_agent(monkeypatch, stream_attr: str) -> Agent:
    async def boom_stream(*args, **kwargs):
        raise _StopLoop

    agent = _make_agent(
        is_sub_agent=False,
        _aborted=False,
        _already_surfaced_memories=set(),
        _session_memory_bytes=0,
    )
    agent._build_side_query = lambda **kwargs: (lambda system, user: "")
    agent._run_compression_pipeline = lambda: None
    setattr(agent, stream_attr, boom_stream)
    monkeypatch.setattr(agent_module, "start_memory_prefetch", lambda *a, **k: _SettledPrefetch())
    monkeypatch.setattr(agent_module, "start_spinner", lambda: None)
    monkeypatch.setattr(agent_module, "stop_spinner", lambda **kwargs: None)
    return agent


def test_openai_memory_injection_failure_is_logged(monkeypatch, caplog):
    agent = _wire_chat_agent(monkeypatch, "_call_openai_stream")
    agent._openai_messages = []
    agent._pending_download_url = None
    agent._download_nudges = 0

    with caplog.at_level(logging.WARNING):
        with pytest.raises(_StopLoop):
            asyncio.run(agent._chat_openai("hi"))

    assert "memory injection failed" in caplog.text


def test_anthropic_memory_injection_failure_is_logged(monkeypatch, caplog):
    agent = _wire_chat_agent(monkeypatch, "_call_anthropic_stream")
    agent._anthropic_messages = []

    with caplog.at_level(logging.WARNING):
        with pytest.raises(_StopLoop):
            asyncio.run(agent._chat_anthropic("hi"))

    assert "memory injection failed" in caplog.text
