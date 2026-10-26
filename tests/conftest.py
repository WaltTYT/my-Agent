import pytest

from agents import skills, tools


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """每个测试使用独立的 HOME 和工作目录，避免读到本机 ~/.lily 配置。"""
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows 下 Path.home() 优先读 USERPROFILE
    # 数据根目录默认取 cwd，这里显式清掉，避免继承本机环境变量导致测试不稳定。
    monkeypatch.delenv("LILY_DATA_DIR", raising=False)
    monkeypatch.chdir(work)
    tools.reset_permission_cache()
    skills.reset_skill_cache()
    yield work
    tools.reset_permission_cache()
    skills.reset_skill_cache()
