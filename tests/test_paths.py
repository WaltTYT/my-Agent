"""运行时数据目录的位置约定。

这些路径决定会话记录和长期记忆落在哪里，拼错一次就等于"用户找不到自己的历史"，
所以用测试钉住：默认位置、环境变量覆盖、以及会话保存与读取确实走同一套目录。
"""

from __future__ import annotations

import json
from pathlib import Path

from agents import paths, session


def test_default_root_is_project_local():
    assert paths.data_root() == Path.cwd() / paths.DEFAULT_DIR_NAME
    assert paths.data_root().is_dir()


def test_env_var_overrides_data_root(tmp_path, monkeypatch):
    target = tmp_path / "shared-data"
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(target))
    assert paths.data_root() == target
    # 根目录应当自动创建，调用方不需要自己 mkdir。
    assert target.is_dir()


def test_every_data_dir_is_nested_under_root():
    root = paths.data_root()
    dirs = [
        paths.sessions_dir(),
        paths.folded_sessions_dir(),
        paths.long_term_dir(),
        paths.plans_dir(),
        paths.tool_results_dir(),
        paths.skill_evolution_dir(),
    ]
    for d in dirs:
        assert d.is_dir(), d
        assert root in d.parents, (root, d)
    # 六类数据各自独立，不能互相覆盖。
    assert len(set(dirs)) == len(dirs)


def test_session_roundtrip_uses_new_location():
    payload = {
        "metadata": {"id": "abc123", "startTime": "2026-10-26T10:00:00Z", "messageCount": 1},
        "anthropicMessages": [{"role": "user", "content": "你好"}],
    }
    session.save_session("abc123", payload)

    path = paths.sessions_dir() / "abc123.json"
    assert path.exists()
    assert session.load_session("abc123")["metadata"]["messageCount"] == 1
    assert session.load_session("missing") is None


def test_folded_memory_lives_under_sessions_dir():
    session.save_folded_session_memory("abc123", {"summary": "讨论了桌面文件"})
    d = paths.folded_sessions_dir()
    assert (d / "abc123.folded-memory.jsonl").exists()
    latest = json.loads((d / "abc123.folded-memory.latest.json").read_text(encoding="utf-8"))
    assert latest["summary"] == "讨论了桌面文件"


def test_get_latest_session_id_picks_newest_start_time():
    session.save_session("older", {"metadata": {"id": "older", "startTime": "2026-01-01T00:00:00Z"}})
    session.save_session("newer", {"metadata": {"id": "newer", "startTime": "2026-06-01T00:00:00Z"}})
    assert session.get_latest_session_id() == "newer"
