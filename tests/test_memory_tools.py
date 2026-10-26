"""长期记忆的写入与删除路径。

记忆以前只能靠模型自己 write_file 手写 frontmatter，既没有确定性的保存入口，
删掉文件后 MEMORY.md 也不会刷新。这里覆盖 save_memory / delete_memory 两个工具，
以及索引自愈和文件名不互相覆盖的约定。
"""

from __future__ import annotations

import asyncio

import pytest

from agents import memory, tools


def run(name: str, inp: dict) -> str:
    return asyncio.run(tools.execute_tool(name, inp, {}))


def save(name: str, description: str, type_: str, content: str) -> str:
    return run("save_memory", {
        "name": name,
        "description": description,
        "type": type_,
        "content": content,
    })


def index_lines() -> list[str]:
    path = memory.get_memory_dir() / "MEMORY.md"
    if not path.exists():
        return []
    return [l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("- ")]


def test_save_memory_writes_file_and_index():
    result = save("包管理器偏好", "只用 pnpm", "user", "安装依赖一律 pnpm install。")
    assert result.startswith("Successfully saved memory:")

    filename = result.split(": ", 1)[1]
    assert (memory.get_memory_dir() / filename).exists()
    assert filename in index_lines()[0]

    entries = memory.list_memories()
    assert [e.filename for e in entries] == [filename]
    assert entries[0].type == "user"
    assert entries[0].description == "只用 pnpm"


def test_distinct_chinese_names_do_not_overwrite_each_other():
    # 早期 _slugify 只保留 a-z0-9，中文名称会被剥成空串，所有中文记忆共用同一个文件名。
    first = save("包管理器偏好", "A", "user", "one").split(": ", 1)[1]
    second = save("构建工具选择", "B", "user", "two").split(": ", 1)[1]

    assert first != second
    assert len(memory.list_memories()) == 2


def test_same_name_updates_in_place():
    first = save("部署流程", "旧描述", "project", "old").split(": ", 1)[1]
    again = save("部署流程", "新描述", "project", "new").split(": ", 1)[1]

    assert first == again
    entries = memory.list_memories()
    assert len(entries) == 1
    assert entries[0].content.strip() == "new"


@pytest.mark.parametrize("bad_type", ["", "nonsense", "USER"])
def test_invalid_type_is_rejected(bad_type: str):
    result = save("标题", "描述", bad_type, "内容")
    assert result.startswith("Error:")
    assert memory.list_memories() == []


def test_missing_name_or_content_is_rejected():
    assert save("", "描述", "user", "内容").startswith("Error:")
    assert save("标题", "描述", "user", "   ").startswith("Error:")


def test_delete_memory_removes_file_and_refreshes_index():
    filename = save("待删除", "x", "feedback", "y").split(": ", 1)[1]
    assert filename in index_lines()[0]

    result = run("delete_memory", {"filename": filename})
    assert result.startswith("Successfully deleted memory:")
    assert not (memory.get_memory_dir() / filename).exists()
    assert index_lines() == []


def test_delete_memory_rejects_traversal_and_index_file():
    assert run("delete_memory", {"filename": "../../../etc/passwd"}).startswith("Error:")
    assert run("delete_memory", {"filename": "MEMORY.md"}).startswith("Error:")
    assert run("delete_memory", {"filename": "missing.md"}).startswith("Error:")


def test_index_self_heals_after_manual_file_removal():
    filename = save("手动清理", "x", "project", "y").split(": ", 1)[1]
    assert filename in index_lines()[0]

    # 模拟用户直接在文件管理器里删掉记忆文件：索引不会自动感知，
    # 但下次组装 system prompt 读索引时应当自愈。
    (memory.get_memory_dir() / filename).unlink()
    assert filename in index_lines()[0]
    assert memory.load_memory_index().count(filename) == 0


def test_memory_tools_are_denied_in_plan_mode():
    inp = {"name": "x", "description": "y", "type": "user", "content": "z"}
    assert tools.check_permission("save_memory", inp, mode="plan")["action"] == "deny"
    assert tools.check_permission("delete_memory", {"filename": "a.md"}, mode="plan")["action"] == "deny"


def test_prompt_section_points_at_the_tool():
    section = memory.build_memory_prompt_section()
    assert "save_memory" in section
    assert "Use the write_file tool to create a memory" not in section
