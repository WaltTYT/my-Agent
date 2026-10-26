"""/remember 与 /forget 的参数解析。

这两个命令存在的意义是让用户不必依赖模型自觉就能落盘记忆，所以解析必须是确定的：
同样的输入永远得到同样的字段，非法输入返回 None 让调用方打印用法。
"""

from __future__ import annotations

import asyncio

import pytest

from agents import main as cli
from agents import memory
from agents.paths import plans_dir


def test_plain_text_becomes_user_memory_with_derived_name():
    fields = cli._parse_remember_args("安装依赖一律用 pnpm，不要 npm")
    assert fields == {
        "name": "安装依赖一律用 pnpm，不要 npm",
        "description": "安装依赖一律用 pnpm，不要 npm",
        "type": "user",
        "content": "安装依赖一律用 pnpm，不要 npm",
    }


def test_leading_type_keyword_is_consumed():
    fields = cli._parse_remember_args("feedback 改代码之前先跑测试")
    assert fields["type"] == "feedback"
    assert fields["content"] == "改代码之前先跑测试"
    assert fields["name"] == "改代码之前先跑测试"


def test_leading_unknown_word_is_kept_as_content():
    # 首词不是合法类型时不能把它吞掉，否则用户内容会凭空少一个词。
    fields = cli._parse_remember_args("badtype 一些内容")
    assert fields["type"] == "user"
    assert fields["content"] == "badtype 一些内容"


def test_pipe_form_fills_every_field():
    fields = cli._parse_remember_args("包管理器 | 只用 pnpm | user | 安装依赖一律 pnpm install")
    assert fields == {
        "name": "包管理器",
        "description": "只用 pnpm",
        "type": "user",
        "content": "安装依赖一律 pnpm install",
    }


@pytest.mark.parametrize("raw,expected_desc,expected_content", [
    ("名称 | 描述", "描述", "名称"),
    ("名称 | 描述 | project", "描述", "名称"),
])
def test_pipe_form_defaults_missing_trailing_fields(raw, expected_desc, expected_content):
    fields = cli._parse_remember_args(raw)
    assert fields["description"] == expected_desc
    assert fields["content"] == expected_content
    assert fields["type"] in memory.VALID_TYPES


@pytest.mark.parametrize("raw", ["", "   ", "| 缺名称 | user | x", "user |  | project | x"])
def test_invalid_input_returns_none(raw):
    assert cli._parse_remember_args(raw) is None


def test_derived_name_collapses_whitespace_and_truncates():
    long_text = "第一行    有很多   空白\n第二行不该出现在标题里\n" + "补" * 50
    name = cli._derive_memory_name(long_text)
    assert name == "第一行 有很多 空白"
    assert len(cli._derive_memory_name("很长的中文" * 30)) == 30
    assert cli._derive_memory_name("   \n  ") == ""


def test_match_memories_prefers_exact_filename():
    filename = memory.save_memory("部署流程", "生产环境部署", "project", "先跑迁移再滚动发布")
    assert [m.filename for m in cli._match_memories(filename)] == [filename]


def test_match_memories_falls_back_to_keyword():
    memory.save_memory("部署流程", "生产环境部署", "project", "先跑迁移再滚动发布")
    memory.save_memory("回滚策略", "回滚要保留数据", "project", "先停流量")

    by_name = cli._match_memories("部署")
    assert [m.name for m in by_name] == ["部署流程"]
    assert len(cli._match_memories("流程")) == 1
    assert cli._match_memories("不存在的东西") == []
    assert cli._match_memories("   ") == []


def test_remember_then_forget_roundtrip():
    fields = cli._parse_remember_args("project 灰度发布先放 5% 流量")
    filename = memory.save_memory(**fields)
    assert filename in {m.filename for m in memory.list_memories()}

    target = cli._match_memories("灰度发布")[0]
    assert memory.delete_memory(target.filename) is True
    assert memory.list_memories() == []
    # 删除后索引里不能再留残项。
    assert "灰度发布" not in memory.load_memory_index()


class _StubAgent:
    """只提供 run_repl 在命令分发上真正会用到的接口，避免为了测试去连模型。"""

    def __init__(self):
        self._aborted = False
        self._output_buffer = None
        self.permission_mode = "default"
        self.chat_requests = []
        self.plan_drafts = []

    def set_confirm_fn(self, fn):
        pass

    def set_plan_approval_fn(self, fn):
        pass

    def abort(self):
        pass

    def toggle_plan_mode(self, task=None):
        self.permission_mode = "default" if self.permission_mode == "plan" else "plan"
        return self.permission_mode

    def create_plan_draft(self, task):
        self.plan_drafts.append(task)
        return "memory/plans/添加联网功能.md"

    async def chat(self, prompt):
        self.chat_requests.append(prompt)

    async def drain_background_skill_tasks(self):
        # REPL 退出前会 await 这个，测试里没有后台任务。
        return None


def _drive_repl(monkeypatch, lines: list[str]) -> _StubAgent:
    """把 lines 喂给真实 REPL 分发，直到 exit 结束。"""
    feed = iter(lines + ["exit"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(feed))
    monkeypatch.setattr(cli, "print_user_prompt", lambda: None)
    agent = _StubAgent()
    asyncio.run(cli.run_repl(agent))
    return agent


def test_remember_and_forget_through_real_repl(monkeypatch, capsys):
    _drive_repl(monkeypatch, [
        "/remember project 灰度发布先放 5% 流量",
        "/memory",
        "/forget 灰度发布",
        "/memory",
    ])
    out = capsys.readouterr().out
    assert "已保存记忆" in out
    assert "灰度发布先放 5% 流量" in out      # /memory 里能看到
    assert "已删除记忆" in out
    assert memory.list_memories() == []


def test_repl_rejects_unparsable_remember(monkeypatch, capsys):
    _drive_repl(monkeypatch, ["/remember", "/remember    "])
    out = capsys.readouterr().out
    assert out.count("用法：/remember") == 2
    assert memory.list_memories() == []


def test_repl_forget_reports_ambiguous_match(monkeypatch, capsys):
    memory.save_memory("部署流程A", "x", "project", "1")
    memory.save_memory("部署流程B", "y", "project", "2")
    _drive_repl(monkeypatch, ["/forget 部署流程"])
    out = capsys.readouterr().out
    assert "匹配到 2 条" in out
    # 有歧义时不能删掉任何东西。
    assert len(memory.list_memories()) == 2


def test_repl_forget_reports_no_match(monkeypatch, capsys):
    _drive_repl(monkeypatch, ["/forget 根本没有这条记忆"])
    assert "没有找到匹配的记忆" in capsys.readouterr().out


def test_plans_lists_plan_files(monkeypatch, capsys):
    plan = plans_dir() / "plan-example.md"
    plan.write_text("# 示例计划\n", encoding="utf-8")

    _drive_repl(monkeypatch, ["/plans"])

    assert "plan-example.md" in capsys.readouterr().out


def test_plans_with_filename_displays_plan_content(monkeypatch, capsys):
    plan = plans_dir() / "plan-example.md"
    plan.write_text("# 示例计划\n第一步", encoding="utf-8")

    _drive_repl(monkeypatch, ["/plans plan-example.md"])

    output = capsys.readouterr().out
    assert "计划文件" in output
    assert "第一步" in output


def test_plans_create_enters_plan_mode_and_chats(monkeypatch):
    agent = _drive_repl(monkeypatch, ["/plans-create 添加联网功能"])

    assert agent.permission_mode == "plan"
    assert agent.plan_drafts == ["添加联网功能"]
    assert len(agent.chat_requests) == 1
    assert "添加联网功能" in agent.chat_requests[0]
    assert "memory/plans/添加联网功能.md" in agent.chat_requests[0]


def test_plan_exit_command_leaves_plan_mode(monkeypatch):
    agent = _drive_repl(monkeypatch, ["/plan", "/plan-exit"])

    assert agent.permission_mode == "default"


def test_plan_exit_reports_when_not_in_plan_mode(monkeypatch, capsys):
    _drive_repl(monkeypatch, ["/plan-exit"])

    assert "当前不在计划模式" in capsys.readouterr().out


def test_plans_create_requires_a_task(monkeypatch, capsys):
    _drive_repl(monkeypatch, ["/plans-create"])

    assert "用法：/plans-create <任务>" in capsys.readouterr().out


def test_plan_with_task_remains_a_compatible_alias(monkeypatch):
    agent = _drive_repl(monkeypatch, ["/plan 重构记忆模块"])

    assert agent.permission_mode == "plan"
    assert agent.chat_requests == ["重构记忆模块"]
