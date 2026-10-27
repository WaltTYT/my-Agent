"""在线技能维护决策的语义：尊重 discard，merge 必须带完整正文。

背景：曾经只要命中同名技能就强制 merge，即使模型返回 discard；并且 merged_instructions
缺失时会退回 candidate.instructions，把原技能正文整段替换成候选内容。这两处都会悄悄
改坏技能库，属于有数据副作用的过度兜底。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import agents.skills as skills_module
from agents.online_skill_evolution import OnlineSkillCandidate, maintain_online_skill_candidate

EXISTING = SimpleNamespace(
    name="demo",
    description="demo description",
    when_to_use="",
    source="project",
    context="",
    prompt_template="OLD BODY",
)


def side_query_returning(payload: dict):
    async def _sq(system: str, user_message: str) -> str:
        import json

        return json.dumps(payload, ensure_ascii=False)

    return _sq


def new_candidate() -> OnlineSkillCandidate:
    return OnlineSkillCandidate(
        name="demo",
        description="demo description",
        instructions="CANDIDATE BODY",
        evidence="user corrected the workflow",
    )


@pytest.fixture
def writes(monkeypatch):
    calls: dict[str, list[dict]] = {"evolve": [], "create": []}
    monkeypatch.setattr(skills_module, "discover_skills", lambda: [EXISTING])
    monkeypatch.setattr(skills_module, "retrieve_relevant_skills", lambda *a, **k: [])

    def evolve(**kwargs):
        calls["evolve"].append(kwargs)
        return {"ok": True, "skill": kwargs["skill_name"], "version": "0.2.0"}

    def create(**kwargs):
        calls["create"].append(kwargs)
        return {"ok": True, "skill": kwargs["name"]}

    monkeypatch.setattr(skills_module, "evolve_skill", evolve)
    monkeypatch.setattr(skills_module, "create_skill", create)
    return calls


def test_discard_is_respected_even_with_an_exact_name_match(writes):
    result = asyncio.run(
        maintain_online_skill_candidate(
            candidate=new_candidate(),
            side_query=side_query_returning({"action": "discard", "reason": "duplicate"}),
        )
    )

    assert result["ok"] is True
    assert result["action"] == "discard"
    assert writes["evolve"] == []
    assert writes["create"] == []


def test_unknown_action_falls_back_to_discard_without_writing(writes):
    result = asyncio.run(
        maintain_online_skill_candidate(
            candidate=new_candidate(),
            side_query=side_query_returning({"action": "rewrite-everything"}),
        )
    )

    assert result["action"] == "discard"
    assert writes["evolve"] == []


def test_merge_without_a_full_body_is_abandoned(writes):
    result = asyncio.run(
        maintain_online_skill_candidate(
            candidate=new_candidate(),
            side_query=side_query_returning({"action": "merge", "target_skill": "demo"}),
        )
    )

    assert result["ok"] is False
    assert result["action"] == "merge"
    assert "merged_instructions" in result["error"]
    assert writes["evolve"] == [], "正文缺失时不能用候选正文覆盖原技能"


def test_add_decision_on_an_existing_skill_also_requires_a_full_body(writes):
    result = asyncio.run(
        maintain_online_skill_candidate(
            candidate=new_candidate(),
            side_query=side_query_returning({"action": "add"}),
        )
    )

    assert result["ok"] is False
    assert result["action"] == "merge"
    assert writes["evolve"] == []
    assert writes["create"] == []


def test_merge_uses_the_supplied_full_body(writes):
    result = asyncio.run(
        maintain_online_skill_candidate(
            candidate=new_candidate(),
            side_query=side_query_returning(
                {"action": "merge", "target_skill": "demo", "merged_instructions": "NEW BODY"}
            ),
        )
    )

    assert result["action"] == "merge"
    assert writes["evolve"][0]["instructions"] == "NEW BODY"
    assert writes["create"] == []
