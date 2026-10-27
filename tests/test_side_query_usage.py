"""side_query 的 token 记账与预算短路。

背景：记忆召回、技能提取、技能使用判定都走 side_query，但返回的 usage 从未累加进
total_input_tokens / total_output_tokens，费用面板长期偏低。这里验证两条路径都记账，
以及预算超限时在发请求前就短路（可用 LILY_SIDE_QUERY_BUDGET=0 关闭）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agents.agent import Agent


def make_agent(**attrs) -> Agent:
    agent = Agent.__new__(Agent)
    agent.model = "glm-4"
    agent.total_input_tokens = 0
    agent.total_output_tokens = 0
    agent.last_input_token_count = 0
    agent.current_turns = 0
    agent.max_cost_usd = None
    agent.max_turns = None
    agent._anthropic_client = None
    agent._openai_client = None
    for key, value in attrs.items():
        setattr(agent, key, value)
    return agent


class AnthropicClient:
    def __init__(self, calls: list[dict]):
        self.calls = calls
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="答案")],
            usage=SimpleNamespace(input_tokens=123, output_tokens=45),
        )


class OpenAIClient:
    def __init__(self, calls: list[dict]):
        self.calls = calls
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="答案"), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=200, completion_tokens=50),
        )


def test_anthropic_side_query_usage_is_counted():
    calls: list[dict] = []
    agent = make_agent(_anthropic_client=AnthropicClient(calls))
    sq = agent._build_side_query()

    text = asyncio.run(sq("system", "user"))

    assert text == "答案"
    assert (agent.total_input_tokens, agent.total_output_tokens) == (123, 45)


def test_openai_side_query_usage_is_counted():
    calls: list[dict] = []
    agent = make_agent(_openai_client=OpenAIClient(calls))
    sq = agent._build_side_query()

    text = asyncio.run(sq("system", "user"))

    assert text == "答案"
    assert (agent.total_input_tokens, agent.total_output_tokens) == (200, 50)


def test_side_query_is_skipped_when_budget_exceeded(monkeypatch):
    monkeypatch.delenv("LILY_SIDE_QUERY_BUDGET", raising=False)
    calls: list[dict] = []
    agent = make_agent(_openai_client=OpenAIClient(calls), max_cost_usd=0.01)
    agent.total_input_tokens = 1_000_000

    text = asyncio.run(agent._build_side_query()("system", "user"))

    assert text == ""
    assert calls == []
    assert agent.total_input_tokens == 1_000_000


def test_budget_gate_can_be_disabled(monkeypatch):
    monkeypatch.setenv("LILY_SIDE_QUERY_BUDGET", "0")
    calls: list[dict] = []
    agent = make_agent(_openai_client=OpenAIClient(calls), max_cost_usd=0.01)
    agent.total_input_tokens = 1_000_000

    text = asyncio.run(agent._build_side_query()("system", "user"))

    assert text == "答案"
    assert len(calls) == 1
    assert agent.total_input_tokens == 1_000_200


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "OFF"])
def test_budget_switch_accepts_common_off_values(monkeypatch, raw):
    monkeypatch.setenv("LILY_SIDE_QUERY_BUDGET", raw)
    agent = make_agent()
    assert agent._side_query_budget_enabled() is False
