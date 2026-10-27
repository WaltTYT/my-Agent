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
