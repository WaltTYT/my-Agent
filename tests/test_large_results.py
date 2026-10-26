"""大结果的分页读取与持久化循环防护。

背景：`read_file` 整份读大文件会超过 `_persist_large_result` 的 30 KB 阈值，旧提示语又
写着 "You can use read_file to see the full result"，而落盘的文件同样超限——模型照着做
就会不断生成新的 tool-results 文件，一次对话烧掉 64 万 input token。

这里的协程统一用 asyncio.run 驱动，不引入 pytest-asyncio：CI 只安装 requirements.txt
加 pytest，多一个测试依赖就要多改一次流水线。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agents import paths, tools
from agents.agent import Agent

PERSIST_THRESHOLD = 30 * 1024


def read(path: str, **kw) -> str:
    inp = {"file_path": path}
    inp.update(kw)
    return asyncio.run(tools.execute_tool("read_file", inp, {}))


def make_agent() -> Agent:
    """绕过 __init__，只给被测方法用到的字段，避免创建模型客户端。"""
    agent = Agent.__new__(Agent)
    agent.use_openai = False
    return agent


@pytest.fixture
def big_file(tmp_path) -> Path:
    """一个整份读入必定超过 30 KB 阈值的文件。"""
    body = "\n".join(f"line_{i}_{x} = compute_value(some_argument)" for i, x in enumerate(range(3000)))
    path = tmp_path / "big.py"
    path.write_text(body + "\n", encoding="utf-8")
    return path


def test_default_page_stays_under_persist_threshold(big_file):
    result = read(str(big_file))
    assert len(result.encode()) < PERSIST_THRESHOLD
    assert "lines shown" in result


def test_line_numbers_match_the_real_file_across_pages(big_file):
    lines = big_file.read_text(encoding="utf-8").splitlines()
    first = read(str(big_file), offset=1234, limit=3).splitlines()[0]
    assert first.startswith("1234 |")
    assert first.split("|", 1)[1].strip() == lines[1233]


def test_last_page_reports_end_of_file(big_file):
    page = read(str(big_file), offset=len(big_file.read_text(encoding="utf-8").splitlines()) - 2,
                limit=tools.MAX_READ_LIMIT)
    assert "End of file" in page
    assert "Continue with offset" not in page


@pytest.mark.parametrize("kw", [{"offset": 0}, {"offset": -5}, {"limit": 0},
                                {"limit": -1}, {"offset": "abc"}, {"limit": None}])
def test_invalid_paging_args_fall_back_safely(big_file, kw):
    result = read(str(big_file), **kw)
    assert not result.startswith("Error")
    assert len(result.encode()) < PERSIST_THRESHOLD


def test_offset_beyond_end_is_clamped_not_crashed(big_file):
    # offset 越界时夹到最后一行返回，而不是报错或返回空，避免模型反复试探。
    # 不硬写行号：以换行结尾的文件是否算出空的末行取决于 split 的实现。
    result = read(str(big_file), offset=10**6)
    assert "End of file" in result
    assert "Continue with offset" not in result
    shown = int(result.splitlines()[0].split("|", 1)[0])
    assert shown >= len(big_file.read_text(encoding="utf-8").splitlines())


def test_persist_message_no_longer_invites_a_reread():
    agent = make_agent()
    big = "some long line of output\n" * 4000
    message = agent._persist_large_result("grep_search", big, {})
    assert "You can use read_file to see the full result" not in message
    assert "Do NOT read_file that whole file" in message
    assert "offset and limit" in message


def test_reading_back_a_persisted_result_does_not_spawn_another():
    agent = make_agent()
    big = "some long line of output\n" * 4000
    first = agent._persist_large_result("grep_search", big, {})
    saved = Path(first.split("saved to ")[1].split(" for the user")[0])
    assert saved.exists()

    before = set(paths.tool_results_dir().glob("*.txt"))
    second = agent._persist_large_result("read_file", big, {"file_path": str(saved)})
    after = set(paths.tool_results_dir().glob("*.txt"))

    assert after == before, "读回落盘文件不该再生成新文件"
    assert "not re-saved" in second


def test_reading_a_normal_large_file_still_persists():
    agent = make_agent()
    big = "some long line of output\n" * 4000
    before = len(list(paths.tool_results_dir().glob("*.txt")))
    agent._persist_large_result("read_file", big, {"file_path": "somewhere/else.py"})
    assert len(list(paths.tool_results_dir().glob("*.txt"))) == before + 1


def test_read_file_schema_exposes_paging():
    schema = next(t for t in tools.tool_definitions if t["name"] == "read_file")["input_schema"]
    assert {"offset", "limit"} <= set(schema["properties"])
    # 不能设成必填，否则所有旧的整读调用都会被打断。
    assert schema["required"] == ["file_path"]
