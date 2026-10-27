"""第 7 条：`/skill-eval` 只读已有报告，`/skill-eval run` 才真正跑评测。

背景：之前 `/skill-eval` 每次都触发完整评测，而候选变体 × 每个 dev 样本各要一轮生成
和一轮判定，串行几十次模型调用。现在查看与运行拆开，样本数、并发都有上限。
"""

from __future__ import annotations

import asyncio
import json

import pytest

import agents.online_skill_eval as ev
from agents import main as cli


class StubAgent:
    def __init__(self, *, fail_side_query: bool = False):
        self.side_query_calls: list[dict] = []
        self.fail_side_query = fail_side_query

    def _build_side_query(self, **kwargs):
        if self.fail_side_query:
            raise AssertionError("/skill-eval 查看报告不应触发模型调用")
        self.side_query_calls.append(kwargs)
        return None

    async def drain_background_skill_tasks(self) -> None:
        pass

    async def close(self) -> None:
        pass

    def set_confirm_fn(self, fn) -> None:
        pass

    def set_plan_approval_fn(self, fn) -> None:
        pass


def run_repl_commands(monkeypatch, commands: list[str], agent: StubAgent) -> list[str]:
    printed: list[str] = []
    lines = iter(commands)

    async def fake_read() -> str:
        return next(lines)

    monkeypatch.setattr(cli, "_read_user_line", fake_read)
    monkeypatch.setattr(cli.signal, "signal", lambda *args: None)
    monkeypatch.setattr(cli, "print_welcome", lambda *a, **k: None)
    monkeypatch.setattr(cli, "print_user_prompt", lambda *a, **k: None)
    monkeypatch.setattr(cli, "print_goodbye", lambda *a, **k: None)
    monkeypatch.setattr(cli, "print_info", lambda *a, **k: printed.append(a[0] if a else ""))
    monkeypatch.setattr(cli, "print_error", lambda *a, **k: printed.append(a[0] if a else ""))

    asyncio.run(cli.run_repl(agent))
    return printed


def test_load_report_reads_persisted_file(monkeypatch, tmp_path):
    monkeypatch.setattr(ev, "get_evolution_dir", lambda: tmp_path)

    assert ev.load_online_skill_eval_report() is None

    (tmp_path / "online_eval_report.json").write_text(json.dumps({"aggregate": {"skills": 0}}), encoding="utf-8")
    assert ev.load_online_skill_eval_report() == {"aggregate": {"skills": 0}}


def test_format_async_never_runs_evaluation(monkeypatch, tmp_path):
    monkeypatch.setattr(ev, "get_evolution_dir", lambda: tmp_path)
    (tmp_path / "online_eval_report.json").write_text(json.dumps({"aggregate": {}}), encoding="utf-8")

    async def fail_eval(**kwargs):
        raise AssertionError("查看报告时不应重新评测")

    monkeypatch.setattr(ev, "evaluate_online_skill_evolution_async", fail_eval)

    assert "Online skill eval" in asyncio.run(ev.format_online_skill_eval_async())


def test_format_async_hints_when_report_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(ev, "get_evolution_dir", lambda: tmp_path)
    assert "/skill-eval run" in asyncio.run(ev.format_online_skill_eval_async())


def test_repl_skill_eval_view_does_not_run_the_model(monkeypatch):
    agent = StubAgent(fail_side_query=True)
    monkeypatch.setattr(cli, "format_online_skill_eval_async", lambda: asyncio.sleep(0, result="VIEW"))

    printed = run_repl_commands(monkeypatch, ["/skill-eval", "exit"], agent)

    assert printed == ["VIEW"]
    assert agent.side_query_calls == []


def test_repl_skill_eval_run_invokes_evaluation(monkeypatch):
    agent = StubAgent()
    seen: dict = {}

    async def fake_eval(**kwargs):
        seen.update(kwargs)
        return {"aggregate": {}}

    monkeypatch.setattr(cli, "evaluate_online_skill_evolution_async", fake_eval)
    monkeypatch.setattr(cli, "format_online_skill_eval", lambda report: "RAN")

    printed = run_repl_commands(monkeypatch, ["/skill-eval run", "exit"], agent)

    assert printed == ["RAN"]
    assert agent.side_query_calls == [{"max_tokens": 2400}]
    assert seen["side_query"] is None


def test_candidate_bundle_caps_samples_per_split(monkeypatch):
    variant = {"variant_id": "v1", "label": "v1", "snapshot": {"name": "demo", "instructions": "do it"}}
    monkeypatch.setattr(ev, "_build_heuristic_candidate_variants", lambda **kwargs: [variant])

    async def no_llm_variant(**kwargs):
        return None

    monkeypatch.setattr(ev, "_build_llm_candidate_variant_async", no_llm_variant)

    calls: list[str] = []

    async def fake_side(system: str, user: str) -> str:
        calls.append(user)
        return "ok"

    samples = [
        {"sample_id": f"dev{i}", "split": "mutate_dev", "messages": [{"role": "user", "content": "hi"}]}
        for i in range(8)
    ] + [
        {"sample_id": f"test{i}", "split": "promotion_test", "messages": [{"role": "user", "content": "hi"}]}
        for i in range(5)
    ]

    bundle = asyncio.run(
        ev._build_candidate_eval_bundle_async(
            lineage_id="lineage",
            snapshot={"name": "demo", "instructions": "do it"},
            replay_pool=samples,
            rules=[],
            rule_summary={},
            side_query=fake_side,
            max_samples_per_split=3,
        )
    )

    assert len(bundle["outputs"]) == 6
    assert len(calls) == 6


def test_generated_variant_respects_the_concurrency_limit():
    active = 0
    max_active = 0

    async def fake_side(system: str, user: str) -> str:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "ok"

    samples = [
        {"sample_id": f"s{i}", "split": "mutate_dev", "messages": [{"role": "user", "content": "hi"}]}
        for i in range(6)
    ]

    asyncio.run(
        ev._evaluate_generated_variant_async(
            variant={"variant_id": "v", "snapshot": {"name": "demo", "instructions": "do it"}},
            samples=samples,
            rules=[],
            side_query=fake_side,
            concurrency=3,
        )
    )

    assert max_active == 3


def test_production_defaults_are_locked():
    import inspect

    bundle = inspect.signature(ev._build_candidate_eval_bundle_async).parameters
    variant = inspect.signature(ev._evaluate_generated_variant_async).parameters
    assert bundle["max_samples_per_split"].default == 3
    assert bundle["concurrency"].default == 3
    assert variant["concurrency"].default == 3
