from agents.frontmatter import format_frontmatter, parse_frontmatter
from agents.skills import create_skill, discover_skills, reset_skill_cache, retrieve_relevant_skills


def write_skill(root, name, description, body):
    d = root / ".lily" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(format_frontmatter({"name": name, "description": description}, body), encoding="utf-8")


def test_frontmatter_round_trip():
    text = format_frontmatter({"name": "demo", "description": "a: b"}, "body line")
    parsed = parse_frontmatter(text)
    assert parsed.meta == {"name": "demo", "description": "a: b"}
    assert parsed.body == "body line"


def test_frontmatter_without_header_returns_body():
    parsed = parse_frontmatter("just text")
    assert parsed.meta == {} and parsed.body == "just text"


def test_user_skill_overrides_project_skill_with_same_name(isolated_env, tmp_path):
    write_skill(isolated_env, "review", "project version", "p")
    write_skill(tmp_path / "home", "review", "user version", "u")
    reset_skill_cache()
    skills = {s.name: s for s in discover_skills()}
    assert skills["review"].description == "user version"
    assert skills["review"].source == "user"


def test_bm25_retrieval_ranks_relevant_skill_first(isolated_env):
    write_skill(isolated_env, "code_review", "Review Python code for bugs", "Check correctness and tests.")
    write_skill(isolated_env, "weekly_report", "写周报", "按项目汇总本周进展和下周计划。")
    reset_skill_cache()
    hits = retrieve_relevant_skills("please review this python code")
    assert hits and hits[0]["name"] == "code_review"
    hits = retrieve_relevant_skills("帮我写一份周报")
    assert hits and hits[0]["name"] == "weekly_report"


def test_retrieval_returns_nothing_for_unrelated_query(isolated_env):
    write_skill(isolated_env, "code_review", "Review Python code for bugs", "Check correctness.")
    reset_skill_cache()
    assert retrieve_relevant_skills("weather tomorrow") == []


# ─── 技能生成质量：禁止空壳 ─────────────────────────────────


def test_create_skill_rejects_empty_instructions(isolated_env):
    """instructions 为空时不能落盘：否则会填成通用模板，生成一个什么都干不了的技能。"""
    result = create_skill("image_gen", "生成图片", instructions="   ")
    assert result["ok"] is False
    assert "instructions" in result["error"]
    assert not (isolated_env / ".lily" / "skills" / "image_gen" / "SKILL.md").exists()


def test_create_skill_accepts_executable_instructions(isolated_env):
    instructions = "1. 用 read_file 读取配置。\n2. 用 write_file 写出结果到 output/report.md。"
    result = create_skill("report_writer", "写报告", instructions=instructions)
    assert result["ok"] is True


def test_inline_skill_activation_says_nothing_executed(isolated_env, monkeypatch):
    """技能激活只注入流程，必须明确告诉模型"还没执行"，否则它会直接宣称已完成。"""
    import asyncio

    import agents.skills as skills_module
    from agents.agent import Agent

    monkeypatch.setattr(skills_module, "execute_skill", lambda name, args: {
        "context": "inline",
        "prompt": "# Workflow\n1. 调用 http_request。",
        "allowed_tools": [],
    })

    agent = Agent.__new__(Agent)
    message = asyncio.run(agent._execute_skill_tool({"skill_name": "generate_image", "args": "小花"}))

    assert "nothing executed yet" in message
    assert "Do not tell the user the task is done" in message


def test_skill_name_called_as_tool_is_routed(isolated_env, monkeypatch):
    """模型常把 generate_image 直接当工具名调用，必须路由到 skill 而不是 Unknown tool。"""
    import asyncio

    import agents.skills as skills_module
    from agents.agent import Agent
    from agents.skills import SkillDefinition

    monkeypatch.setattr(skills_module, "discover_skills", lambda: [
        SkillDefinition(name="generate_image", description="draw", user_invocable=False),
    ])
    monkeypatch.setattr(skills_module, "execute_skill", lambda name, args: {
        "context": "inline",
        "prompt": "# Workflow\n1. 调用 http_request。",
        "allowed_tools": [],
    })

    agent = Agent.__new__(Agent)
    agent.tools = []
    message = asyncio.run(agent._execute_tool_call(
        "generate_image", {"args": "小猪猪", "skill_name": "generate_image"}
    ))

    assert "nothing executed yet" in message


def test_extract_download_url_finds_file_urls():
    """API 返回的图片 URL 要被识别出来，普通接口 URL 不算。"""
    from agents.agent import Agent

    cogview = (
        'HTTP 200 (application/json)\n{"data":[{"url":'
        '"https://example.com/20261027_watermark.png?Signature=abc%3D&Expires=123"}]}'
    )
    assert Agent._extract_download_url(cogview) == (
        "https://example.com/20261027_watermark.png?Signature=abc%3D&Expires=123"
    )
    assert Agent._extract_download_url('{"url":"https://api.example.com/v4/chat"}') is None
    assert Agent._extract_download_url("") is None


def test_http_request_without_save_to_marks_pending_download(isolated_env, monkeypatch):
    """未保存的 http_request 要留下待下载标记，保存成功后要清掉。"""
    import asyncio

    import agents.skills as skills_module
    from agents.agent import Agent

    monkeypatch.setattr(skills_module, "discover_skills", lambda: [])

    async def fake_execute_tool(name, *args, **kwargs):
        return 'HTTP 200\n{"data":[{"url":"https://example.com/a.png"}]}'

    monkeypatch.setattr("agents.agent.execute_tool", fake_execute_tool)

    agent = Agent.__new__(Agent)
    agent.tools = []
    agent._pending_download_url = None
    agent._read_file_state = {}
    agent._mcp_manager = type("M", (), {"is_mcp_tool": lambda self, name: False})()

    asyncio.run(agent._execute_tool_call(
        "http_request", {"method": "POST", "url": "https://api.example.com/gen"}
    ))
    assert agent._pending_download_url == "https://example.com/a.png"

    asyncio.run(agent._execute_tool_call(
        "http_request", {"method": "GET", "url": "https://example.com/a.png", "save_to": "~/Desktop/a.png"}
    ))
    assert agent._pending_download_url is None


def test_unknown_tool_is_not_routed(isolated_env, monkeypatch):
    import asyncio

    import agents.skills as skills_module
    from agents.agent import Agent

    monkeypatch.setattr(skills_module, "discover_skills", lambda: [])
    async def fake_execute_tool(name, *args, **kwargs):
        return f"Unknown tool: {name}"

    monkeypatch.setattr("agents.agent.execute_tool", fake_execute_tool)

    agent = Agent.__new__(Agent)
    agent.tools = []
    agent._read_file_state = {}
    agent._mcp_manager = type("M", (), {"is_mcp_tool": lambda self, name: False})()
    message = asyncio.run(agent._execute_tool_call("not_a_skill", {}))

    assert message == "Unknown tool: not_a_skill"
