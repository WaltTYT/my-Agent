import asyncio
import os

from agents.tools import execute_tool


def run(name, inp, state):
    return asyncio.run(execute_tool(name, inp, state))


def test_edit_requires_reading_first(isolated_env):
    (isolated_env / "a.py").write_text("x = 1\n")
    result = run("edit_file", {"file_path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}, {})
    assert result.startswith("Error: You must read this file")
    assert (isolated_env / "a.py").read_text() == "x = 1\n"


def test_read_then_edit_succeeds(isolated_env):
    (isolated_env / "a.py").write_text("x = 1\n")
    state = {}
    run("read_file", {"file_path": "a.py"}, state)
    result = run("edit_file", {"file_path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}, state)
    assert result.startswith("Successfully edited")
    assert (isolated_env / "a.py").read_text() == "x = 2\n"


def test_external_modification_forces_reread(isolated_env):
    path = isolated_env / "a.py"
    path.write_text("x = 1\n")
    state = {}
    run("read_file", {"file_path": "a.py"}, state)
    path.write_text("x = 100\n")
    mtime = os.path.getmtime(path)
    os.utime(path, (mtime + 5, mtime + 5))
    result = run("edit_file", {"file_path": "a.py", "old_string": "x = 100", "new_string": "x = 2"}, state)
    assert "modified externally" in result
    assert path.read_text() == "x = 100\n"


def test_consecutive_edits_do_not_need_reread(isolated_env):
    (isolated_env / "a.py").write_text("a = 1\nb = 1\n")
    state = {}
    run("read_file", {"file_path": "a.py"}, state)
    run("edit_file", {"file_path": "a.py", "old_string": "a = 1", "new_string": "a = 2"}, state)
    result = run("edit_file", {"file_path": "a.py", "old_string": "b = 1", "new_string": "b = 2"}, state)
    assert result.startswith("Successfully edited")


def test_edit_rejects_ambiguous_old_string(isolated_env):
    (isolated_env / "a.py").write_text("x = 1\nx = 1\n")
    state = {}
    run("read_file", {"file_path": "a.py"}, state)
    result = run("edit_file", {"file_path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}, state)
    assert "Must be unique" in result


def test_edit_matches_through_curly_quotes(isolated_env):
    (isolated_env / "a.py").write_text("print(“hello”)\n", encoding="utf-8")
    state = {}
    run("read_file", {"file_path": "a.py"}, state)
    result = run("edit_file", {"file_path": "a.py", "old_string": 'print("hello")', "new_string": 'print("hi")'}, state)
    assert "quote normalization" in result
    assert (isolated_env / "a.py").read_text(encoding="utf-8") == 'print("hi")\n'


def test_unknown_tool():
    assert run("no_such_tool", {}, None) == "Unknown tool: no_such_tool"
