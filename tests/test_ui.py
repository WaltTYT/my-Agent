import io
import sys

from agents import ui


def test_print_cost_starts_on_a_new_line(monkeypatch):
    calls = []
    monkeypatch.setattr(ui.console, "print", lambda *args, **kwargs: calls.append((args, kwargs)))

    ui.print_cost(100, 20)

    assert calls[0] == ((), {})
    assert isinstance(calls[1][0][0], ui.Panel)
    assert calls[1][0][0].title == "费用"


def test_tool_call_panel_starts_on_fresh_line_after_streamed_text(monkeypatch):
    """流式输出停在行中间时，工具调用面板必须从新的一行绘制，否则边框会错位折行。"""
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)

    ui.print_assistant_text("我将使用工具来查询。请稍等一下。")
    ui.print_tool_call("search_web", {"query": "test"})

    out = buf.getvalue()
    lines = out.split("\n")
    assert lines[0].endswith("请稍等一下。")
    assert lines[1].startswith(("\u256d", "\u250c")), f"panel top border must start at col 0, got {lines[1][:20]!r}"


def test_no_extra_blank_line_when_line_already_fresh(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)

    ui.print_assistant_text("已经换行结束。\n")
    ui.print_tool_call("search_web", {"query": "test"})

    out = buf.getvalue()
    assert not out.startswith("\n")
    lines = out.split("\n")
    assert lines[0] == "已经换行结束。"
    assert lines[1].startswith(("\u256d", "\u250c"))
