"""Terminal UI rendering — colored output, spinner, tool display."""

from __future__ import annotations

import sys
import threading
import time

from rich import box
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console(highlight=False)

# ─── Basic output ──────────────────────────────────────────


def _safe_text(value: object) -> str:
    return str(value).encode("utf-8", errors="replace").decode("utf-8")


def _safe_stdout_write(text: object) -> None:
    sys.stdout.write(_safe_text(text))
    sys.stdout.flush()


COOKIE_BEAR = r"""
        _     _
      _( )___( )_
     /  o     o  \
    |      ^      |
    |   \_____/   |
     \  .-.-.    /
      '-.....---'
"""


def print_welcome() -> None:
    title = Text("Lily", style="bold #f6c177")
    subtitle = Text("可进化的智能体 CLI", style="bold cyan")
    cookie = Text(COOKIE_BEAR, style="bold #d19a66")

    commands = Table.grid(padding=(0, 2))
    commands.add_column(style="bold cyan", no_wrap=True)
    commands.add_column(style="dim")
    commands.add_row("/plan", "只读规划工作流")
    commands.add_row("/skills", "列出可复用技能")
    commands.add_row("/skill-create", "创建可复用技能")
    commands.add_row("/skill-stats", "查看技能进化统计")
    commands.add_row("/memory", "列出长期记忆")
    commands.add_row("/remember", "强制保存一条长期记忆")
    commands.add_row("/forget", "按名称或文件名删除记忆")
    commands.add_row("/compact", "压缩当前上下文")
    commands.add_row("exit", "退出会话")

    body = Table.grid()
    body.add_row(Align.center(cookie))
    body.add_row(Align.center(title))
    body.add_row(Align.center(subtitle))
    body.add_row("")
    body.add_row(Panel(commands, title="快捷命令", border_style="cyan", box=box.ROUNDED))

    console.print()
    console.print(Panel(
        body,
        title="[bold #f6c177] Lily 已就绪 [/bold #f6c177]",
        subtitle="[dim]请在下方输入你的需求[/dim]",
        border_style="#d19a66",
        box=box.ROUNDED,
        padding=(1, 2),
    ))
    console.print()


def print_user_prompt() -> None:
    console.print("\n[bold #f6c177]Lily[/bold #f6c177] [dim]❯[/dim] ", end="")


def print_assistant_text(text: str) -> None:
    _safe_stdout_write(text)


def print_tool_call(name: str, inp: dict) -> None:
    icon = _get_tool_icon(name)
    summary = _get_tool_summary(name, inp)
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold yellow", no_wrap=True)
    table.add_column(style="white")
    table.add_row("工具", f"{icon} {name}")
    if summary:
        table.add_row("输入", _safe_text(summary))
    console.print(Panel(
        table,
        title="[bold yellow]工具调用[/bold yellow]",
        border_style="yellow",
        box=box.ROUNDED,
        padding=(0, 1),
    ))


def print_tool_result(name: str, result: str) -> None:
    result = _safe_text(result)
    if (name in ("edit_file", "write_file")) and not result.startswith("Error"):
        _print_file_change_result(name, result)
        return
    max_len = 500
    truncated = result
    if len(result) > max_len:
        truncated = result[:max_len] + f"\n  ……（共 {len(result)} 字符）"
    console.print(Panel(
        _safe_text(truncated),
        title=f"[dim]{name} 结果[/dim]",
        border_style="dim",
        box=box.ROUNDED,
        padding=(0, 1),
    ))


def _print_file_change_result(_name: str, result: str) -> None:
    lines = result.split("\n")
    console.print(Panel(
        _safe_text(lines[0]),
        title="[bold green]文件变更[/bold green]",
        border_style="green",
        box=box.ROUNDED,
        padding=(0, 1),
    ))

    max_display = 40
    content_lines = lines[1:]
    display_lines = content_lines[:max_display]

    for line in display_lines:
        if not line.strip():
            continue
        if line.startswith("@@"):
            console.print(f"[cyan]  {line}[/cyan]")
        elif line.startswith("- "):
            console.print(f"[red]  {line}[/red]")
        elif line.startswith("+ "):
            console.print(f"[green]  {line}[/green]")
        else:
            console.print(f"[dim]  {line}[/dim]")
    if len(content_lines) > max_display:
        console.print(f"[dim]  ……（还有 {len(content_lines) - max_display} 行）[/dim]")


def print_error(msg: str) -> None:
    console.print(Panel(
        _safe_text(msg),
        title="[bold red]错误[/bold red]",
        border_style="red",
        box=box.ROUNDED,
        padding=(0, 1),
    ))


def print_confirmation(command: str) -> None:
    console.print(Panel(
        _safe_text(command),
        title="[bold yellow]危险命令[/bold yellow]",
        border_style="yellow",
        box=box.ROUNDED,
        padding=(0, 1),
    ))


def print_divider() -> None:
    console.rule("[dim]本轮完成[/dim]", style="dim")


def print_cost(input_tokens: int, output_tokens: int) -> None:
    cost_in = (input_tokens / 1_000_000) * 3
    cost_out = (output_tokens / 1_000_000) * 15
    total = cost_in + cost_out
    table = Table.grid(padding=(0, 2))
    table.add_column(style="cyan")
    table.add_column(style="white")
    table.add_row("输入", f"{input_tokens} tokens")
    table.add_row("输出", f"{output_tokens} tokens")
    table.add_row("预估", f"${total:.4f}")
    # 流式助手输出可能没有以换行结束，费用面板必须从下一行开始绘制。
    console.print()
    console.print(Panel(table, title="费用", border_style="cyan", box=box.ROUNDED))


def print_retry(attempt: int, max_retries: int, reason: str) -> None:
    console.print(_safe_text(f"\n  [yellow]↻ 重试 {attempt}/{max_retries}：{reason}[/yellow]"))


def print_info(msg: str) -> None:
    console.print(Panel(
        _safe_text(msg),
        title="[bold cyan]信息[/bold cyan]",
        border_style="cyan",
        box=box.ROUNDED,
        padding=(0, 1),
    ))


def print_warning(msg: str) -> None:
    console.print(Panel(
        _safe_text(msg),
        title="[bold yellow]提示[/bold yellow]",
        border_style="yellow",
        box=box.ROUNDED,
        padding=(0, 1),
    ))


def print_goodbye() -> None:
    console.print(Panel(
        Text("再见，下次 Lily 再会。", style="bold #f6c177"),
        border_style="#d19a66",
        box=box.ROUNDED,
        padding=(0, 1),
    ))


def print_interrupted() -> None:
    print_warning("已中断。再按一次 Ctrl+C 退出。")


# ─── Spinner ──────────────────────────────────────────────

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

_spinner_thread: threading.Thread | None = None
_spinner_stop = threading.Event()


def start_spinner(label: str = "思考中") -> None:
    global _spinner_thread
    if _spinner_thread is not None:
        return
    _spinner_stop.clear()

    def _run() -> None:
        frame = 0
        _safe_stdout_write(f"\n  {SPINNER_FRAMES[0]} {label}...")
        while not _spinner_stop.is_set():
            time.sleep(0.08)
            frame = (frame + 1) % len(SPINNER_FRAMES)
            _safe_stdout_write(f"\r  {SPINNER_FRAMES[frame]} {label}...")

    _spinner_thread = threading.Thread(target=_run, daemon=True)
    _spinner_thread.start()


def stop_spinner(*, newline: bool = False) -> None:
    global _spinner_thread
    if _spinner_thread is None:
        return
    _spinner_stop.set()
    _spinner_thread.join(timeout=1)
    _spinner_thread = None
    _safe_stdout_write("\r\033[K")
    if newline:
        _safe_stdout_write("\n")


# ─── Plan approval display ──────────────────────────────────


def print_plan_for_approval(plan_content: str) -> None:
    lines = plan_content.split("\n")
    max_lines = 60
    preview = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        preview += f"\n\n……（还有 {len(lines) - max_lines} 行）"
    console.print(Panel(
        _safe_text(preview),
        title="[bold cyan]待审批的计划[/bold cyan]",
        border_style="cyan",
        box=box.ROUNDED,
        padding=(1, 2),
    ))


def print_plan_approval_options() -> None:
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column("choice", style="bold yellow", no_wrap=True)
    table.add_column("action", style="white")
    table.add_column("detail", style="dim")
    table.add_row("1", "清空上下文并执行", "全新开始，自动接受编辑")
    table.add_row("2", "执行", "保留上下文，自动接受编辑")
    table.add_row("3", "逐条确认编辑", "保留上下文，每次编辑都需确认")
    table.add_row("4", "继续规划", "提供反馈以修订计划")
    console.print(Panel(table, title="[bold yellow]请选择[/bold yellow]", border_style="yellow", box=box.ROUNDED))


# ─── Sub-agent display ──────────────────────────────────────


def print_sub_agent_start(agent_type: str, description: str) -> None:
    console.print(Panel(
        _safe_text(description),
        title=f"[bold magenta]子代理启动：{agent_type}[/bold magenta]",
        border_style="magenta",
        box=box.ROUNDED,
        padding=(0, 1),
    ))


def print_sub_agent_end(agent_type: str, _description: str) -> None:
    console.print(Panel(
        "已完成",
        title=f"[bold magenta]子代理结束：{agent_type}[/bold magenta]",
        border_style="magenta",
        box=box.ROUNDED,
        padding=(0, 1),
    ))


def print_memory_entries(memories: list[object]) -> None:
    table = Table(box=box.ROUNDED, header_style="bold cyan", border_style="cyan")
    table.add_column("类型", style="bold #f6c177", no_wrap=True)
    table.add_column("名称", style="white")
    table.add_column("描述", style="dim")
    for m in memories:
        table.add_row(
            _safe_text(getattr(m, "type", "")),
            _safe_text(getattr(m, "name", "")),
            _safe_text(getattr(m, "description", "")),
        )
    console.print(Panel(table, title="[bold cyan]记忆[/bold cyan]", border_style="cyan", box=box.ROUNDED))


def print_skill_entries(skills: list[object]) -> None:
    table = Table(box=box.ROUNDED, header_style="bold cyan", border_style="cyan")
    table.add_column("技能", style="bold #f6c177", no_wrap=True)
    table.add_column("来源", style="cyan", no_wrap=True)
    table.add_column("模式", style="magenta", no_wrap=True)
    table.add_column("描述", style="white")
    for s in skills:
        name = getattr(s, "name", "")
        tag = f"/{name}" if getattr(s, "user_invocable", False) else name
        table.add_row(
            _safe_text(tag),
            _safe_text(getattr(s, "source", "")),
            _safe_text(getattr(s, "context", "")),
            _safe_text(getattr(s, "description", "")),
        )
    console.print(Panel(table, title="[bold cyan]技能[/bold cyan]", border_style="cyan", box=box.ROUNDED))


# ─── Tool icons and summaries ───────────────────────────────

_TOOL_ICONS = {
    "read_file": "📖",
    "write_file": "✏️",
    "edit_file": "🔧",
    "list_files": "📁",
    "grep_search": "🔍",
    "run_shell": "💻",
    "skill": "⚡",
    "skill_create": "🍪",
    "skill_evolve": "🧬",
    "agent": "🤖",
}


def _get_tool_icon(name: str) -> str:
    return _TOOL_ICONS.get(name, "🔨")


def _get_tool_summary(name: str, inp: dict) -> str:
    if name == "read_file":
        return inp.get("file_path", "")
    if name == "write_file":
        return inp.get("file_path", "")
    if name == "edit_file":
        return inp.get("file_path", "")
    if name == "list_files":
        return inp.get("pattern", "")
    if name == "grep_search":
        return f'"{inp.get("pattern", "")}" in {inp.get("path", ".")}'
    if name == "run_shell":
        cmd = inp.get("command", "")
        return cmd[:60] + "..." if len(cmd) > 60 else cmd
    if name == "skill":
        return inp.get("skill_name", "")
    if name == "skill_create":
        return inp.get("name", "")
    if name == "skill_evolve":
        return inp.get("skill_name", "")
    if name == "agent":
        return f'[{inp.get("type", "general")}] {inp.get("description", "")}'
    return ""
