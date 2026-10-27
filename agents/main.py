"""CLI entry point and interactive REPL — mirrors cli.ts."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import signal
import sys
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from dotenv import find_dotenv, load_dotenv

from .ui import (
    print_welcome,
    print_user_prompt,
    print_error,
    print_info,
    print_plan_for_approval,
    print_plan_approval_options,
    print_goodbye,
    print_interrupted,
    print_memory_entries,
    print_skill_entries,
    print_warning,
)
from .session import load_session, get_latest_session_id
from .memory import VALID_TYPES, MemoryEntry, delete_memory, list_memories, save_memory
from .skills import (
    create_skill,
    discover_skills,
    evolve_skill,
    execute_skill,
    get_skill_by_name,
    record_feedback,
    skill_stats,
)
from .online_skill_eval import format_online_skill_eval_async
from .paths import plans_dir

if TYPE_CHECKING:
    # 只为类型注解存在。运行时在 main() 里延迟导入，避免 --help 和测试收集
    # 付 anthropic + openai 两个 SDK 约 4 秒的导入开销。
    from .agent import Agent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mini-claude",
        description="Lily — a minimal coding agent",
        add_help=False,
    )
    parser.add_argument("prompt", nargs="*", help="One-shot prompt")
    parser.add_argument("--yolo", "-y", action="store_true", help="Skip all confirmation prompts")
    parser.add_argument("--plan", action="store_true", help="Plan mode: read-only")
    parser.add_argument("--accept-edits", action="store_true", help="Auto-approve file edits")
    parser.add_argument("--dont-ask", action="store_true", help="Auto-deny confirmations (for CI)")
    parser.add_argument("--thinking", action="store_true", help="Enable extended thinking")
    parser.add_argument("--model", "-m", default=None, help="Model to use")
    parser.add_argument("--api-base", default=None, help="OpenAI-compatible API base URL")
    parser.add_argument("--resume", action="store_true", help="Resume last session")
    parser.add_argument("--max-cost", type=float, default=None, help="Max USD spend")
    parser.add_argument("--max-turns", type=int, default=None, help="Max agentic turns")
    parser.add_argument("--help", "-h", action="store_true", help="Show help")
    return parser.parse_args()


def _resolve_permission_mode(args: argparse.Namespace) -> str:
    if args.yolo:
        return "bypassPermissions"
    if args.plan:
        return "plan"
    if args.accept_edits:
        return "acceptEdits"
    if args.dont_ask:
        return "dontAsk"
    return "default"


def _clean_env(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _load_env_file() -> None:
    env_path = find_dotenv(usecwd=True)
    if env_path:
        load_dotenv(env_path, override=False)
    else:
        load_dotenv(override=False)


def _is_anthropic_compatible_base_url(base_url: str | None) -> bool:
    if not base_url:
        return False
    parsed = urlparse(base_url)
    path = (parsed.path or "").lower().rstrip("/")
    return path.endswith("/anthropic") or "/anthropic/" in path


def _resolve_api_config(cli_api_base: str | None) -> tuple[str | None, str | None, bool]:
    generic_api_key = _clean_env(os.environ.get("APIKEY")) or _clean_env(os.environ.get("MINI_CLAUDE_API_KEY"))
    openai_api_key = _clean_env(os.environ.get("OPENAI_API_KEY"))
    anthropic_api_key = _clean_env(os.environ.get("ANTHROPIC_API_KEY"))

    generic_api_base = _clean_env(os.environ.get("API")) or _clean_env(os.environ.get("MINI_CLAUDE_API_BASE"))
    openai_api_base = _clean_env(os.environ.get("OPENAI_BASE_URL"))
    anthropic_api_base = _clean_env(os.environ.get("ANTHROPIC_BASE_URL"))

    resolved_api_base = _clean_env(cli_api_base) or generic_api_base or openai_api_base or anthropic_api_base

    if resolved_api_base:
        if _is_anthropic_compatible_base_url(resolved_api_base):
            return resolved_api_base, generic_api_key or anthropic_api_key or openai_api_key, False
        return resolved_api_base, generic_api_key or openai_api_key or anthropic_api_key, True

    if anthropic_api_key or anthropic_api_base:
        return anthropic_api_base, generic_api_key or anthropic_api_key or openai_api_key, False

    if openai_api_key or openai_api_base:
        return openai_api_base, generic_api_key or openai_api_key or anthropic_api_key, True

    if generic_api_key:
        return None, generic_api_key, False

    return None, None, False


def _derive_memory_name(content: str) -> str:
    """从记忆正文里取一行当标题：压缩空白并限制长度，避免标题里塞进整段内容。"""
    stripped = content.strip()
    if not stripped:
        return ""
    first_line = stripped.splitlines()[0]
    return re.sub(r"\s+", " ", first_line).strip()[:30]


def _parse_remember_args(raw: str) -> dict[str, str] | None:
    """把 /remember 的参数解析成 save_memory 需要的字段，失败返回 None。

    支持三种写法，字段缺省时逐级回退：
      /remember <内容>                          类型默认 user，标题和描述从内容推导
      /remember <类型> <内容>                    首词是合法类型时按它归类
      /remember <名称> | <描述> | <类型> | <内容>  全手工，末尾字段可省略但中间不能留空
    这里刻意不调模型，保证用户说"记住"就一定写得下去。
    """
    text = raw.strip()
    if not text:
        return None

    if "|" in text:
        parts = [part.strip() for part in text.split("|", 3)]
        # 允许省略末尾字段，但中间留空说明写错了，不能静默拿前一个字段顶上。
        last_filled = max(i for i, part in enumerate(parts) if part)
        if any(not part for part in parts[:last_filled + 1]):
            return None
        name = parts[0]
        description = parts[1] if len(parts) > 1 and parts[1] else name
        mem_type = parts[2] if len(parts) > 2 and parts[2] else "user"
        content = parts[3] if len(parts) > 3 and parts[3] else name
    else:
        head, _, tail = text.partition(" ")
        if tail.strip() and head.lower() in VALID_TYPES:
            mem_type, content = head.lower(), tail.strip()
        else:
            mem_type, content = "user", text
        name = _derive_memory_name(content)
        description = name
        if not name:
            return None

    if mem_type not in VALID_TYPES:
        return None
    return {"name": name, "description": description, "type": mem_type, "content": content}


def _match_memories(query: str) -> list[MemoryEntry]:
    """按文件名或关键词查找记忆，省得用户必须记住 user_xxx.md 这种文件名。"""
    q = query.strip().lower()
    memories = list_memories()
    if not q:
        return []
    exact = [m for m in memories if m.filename.lower() == q]
    if exact:
        return exact
    return [
        m for m in memories
        if q in m.name.lower() or q in m.description.lower() or q in m.filename.lower()
    ]


def _show_plans(query: str = "") -> None:
    """列出或查看 `memory/plans/` 下的任务计划。"""
    files = sorted(
        (path for path in plans_dir().glob("*.md") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    query = query.strip().lower()
    if not query:
        if not files:
            print_info("还没有计划文件。使用 /plans-create <任务> 创建一份计划。")
            return
        lines = ["任务计划（memory/plans）："]
        lines.extend(f"- {path.name}（{path.stat().st_size} bytes）" for path in files)
        lines.append("\n使用 /plans <文件名> 查看具体计划。")
        print_info("\n".join(lines))
        return

    matches = [path for path in files if path.name.lower() == query]
    if not matches:
        matches = [path for path in files if query in path.name.lower()]
    if not matches:
        print_error(f"没有找到计划文件：{query}")
        return
    if len(matches) > 1:
        print_warning("匹配到多个计划文件，请使用完整文件名：\n" + "\n".join(f"- {p.name}" for p in matches))
        return
    path = matches[0]
    try:
        content = path.read_text(encoding="utf-8") or "（计划文件目前为空）"
    except OSError as exc:
        print_error(f"读取计划文件失败：{exc}")
        return
    print_info(f"计划文件：{path}\n\n{content}")


async def run_repl(agent: Agent) -> None:
    """Interactive REPL loop."""

    async def confirm_fn(message: str) -> bool:
        try:
            answer = input("  是否允许？(y/n): ")
            return answer.lower().startswith("y")
        except EOFError:
            return False

    agent.set_confirm_fn(confirm_fn)

    async def plan_approval_fn(plan_content: str) -> dict:
        print_plan_for_approval(plan_content)
        print_plan_approval_options()
        while True:
            try:
                choice = input("  请输入选项编号 (1-4): ").strip()
            except EOFError:
                return {"choice": "manual-execute"}
            if choice == "1":
                return {"choice": "clear-and-execute"}
            elif choice == "2":
                return {"choice": "execute"}
            elif choice == "3":
                return {"choice": "manual-execute"}
            elif choice == "4":
                try:
                    feedback = input("  反馈（需要改什么）: ").strip()
                except EOFError:
                    feedback = ""
                return {"choice": "keep-planning", "feedback": feedback or None}
            else:
                print_warning("无效选项。请输入 1、2、3 或 4。")

    agent.set_plan_approval_fn(plan_approval_fn)

    sigint_count = 0

    def handle_sigint(sig, frame):
        nonlocal sigint_count
        if agent._aborted is False and agent._output_buffer is not None:
            # Agent is processing
            agent.abort()
            print_interrupted()
            sigint_count = 0
            print_user_prompt()
        else:
            sigint_count += 1
            if sigint_count >= 2:
                print_goodbye()
                sys.exit(0)
            print_warning("再按一次 Ctrl+C 退出。")
            print_user_prompt()

    signal.signal(signal.SIGINT, handle_sigint)
    print_welcome()

    while True:
        print_user_prompt()
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            print_goodbye()
            break

        inp = line.strip()
        sigint_count = 0

        if not inp:
            continue
        if inp in ("exit", "quit"):
            print_goodbye()
            break

        # REPL commands
        if inp == "/clear":
            agent.clear_history()
            continue
        if inp == "/plans" or inp.startswith("/plans "):
            _show_plans(inp[len("/plans"):].strip())
            continue
        if inp == "/plans-create" or inp.startswith("/plans-create "):
            request = inp[len("/plans-create"):].strip()
            if not request:
                print_error("用法：/plans-create <任务>")
                continue
            snapshot = agent.plan_state_snapshot()
            try:
                if agent.permission_mode == "plan":
                    agent.toggle_plan_mode()
                agent.toggle_plan_mode(request)
                plan_path = agent.create_plan_draft(request)
            except Exception as exc:
                print_error(f"创建计划失败：{exc}")
                # 反向 toggle 只能恢复模式，会丢掉原本绑定的计划文件，按快照整体回滚。
                agent.restore_plan_state(snapshot)
                continue
            try:
                await agent.chat(
                    f"请直接完善当前计划文件，不要创建其他计划文件。"
                    f"当前计划文件是：{plan_path}\n"
                    f"任务：{request}\n"
                    "请基于初稿补充背景、实施步骤、验证方式、风险与假设；"
                    "缺少信息时写明合理假设，不要向我追问。"
                )
            except Exception as exc:
                # 草稿已落盘，保留计划模式让用户能接着改；只报错，不退出 REPL。
                if "abort" not in str(exc).lower():
                    print_error(str(exc))
            continue
        if inp == "/plan":
            agent.toggle_plan_mode()
            continue
        if inp == "/plan-exit":
            if agent.permission_mode != "plan":
                print_info("当前不在计划模式，无需退出。")
                continue
            agent.toggle_plan_mode()
            continue
        if inp.startswith("/plan "):
            request = inp[len("/plan"):].strip()
            if agent.permission_mode != "plan":
                agent.toggle_plan_mode(request)
            await agent.chat(request)
            continue
        if inp == "/cost":
            agent.show_cost()
            continue
        if inp == "/compact":
            try:
                await agent.compact()
            except Exception as e:
                print_error(str(e))
            continue
        if inp == "/memory":
            memories = list_memories()
            if not memories:
                print_info("还没有保存任何记忆。")
            else:
                print_memory_entries(memories)
            continue
        if inp == "/remember" or inp.startswith("/remember "):
            fields = _parse_remember_args(inp[len("/remember"):])
            if not fields:
                print_error("用法：/remember <内容>  或  /remember <名称> | <描述> | <类型> | <内容>")
                continue
            try:
                filename = save_memory(**fields)
            except ValueError as e:
                print_error(str(e))
                continue
            print_info(f"已保存记忆：{fields['name']}（{filename}）")
            continue
        if inp == "/forget" or inp.startswith("/forget "):
            query = inp[len("/forget"):].strip()
            if not query:
                print_error("用法：/forget <文件名或关键词>")
                continue
            matches = _match_memories(query)
            if not matches:
                print_error(f"没有找到匹配的记忆：{query}")
                continue
            if len(matches) > 1:
                print_warning(f"匹配到 {len(matches)} 条，请用更具体的关键词或直接给文件名：")
                print_memory_entries(matches)
                continue
            target = matches[0]
            if delete_memory(target.filename):
                print_info(f"已删除记忆：{target.name}（{target.filename}）")
            else:
                print_error(f"删除失败：{target.filename}")
            continue
        if inp == "/skills":
            skills = discover_skills()
            if not skills:
                print_info("未找到技能。请将技能添加到 .lily/skills/<名称>/SKILL.md")
            else:
                print_skill_entries(skills)
            continue
        if inp == "/skill-stats":
            print_info(skill_stats())
            continue
        if inp == "/skill-eval":
            side_query = agent._build_side_query(max_tokens=2400)
            print_info(await format_online_skill_eval_async(side_query=side_query))
            continue
        if inp.startswith("/extract_now"):
            hint = inp[len("/extract_now") :].strip()
            result = await agent.extract_now(hint)
            if result.get("ok"):
                print_info("已对当前待处理的在线技能窗口执行提取。")
            else:
                print_error(str(result.get("error") or result))
            continue
        if inp.startswith("/skill-feedback "):
            _, rest = inp.split(" ", 1)
            parts = rest.strip().split(" ", 2)
            if len(parts) < 2:
                print_error("用法：/skill-feedback <技能名> <评分> [备注]")
                continue
            note = parts[2] if len(parts) > 2 else ""
            record_feedback(parts[0], parts[1], note)
            print_info(f"已记录技能反馈：{parts[0]}")
            continue
        if inp.startswith("/skill-evolve "):
            _, rest = inp.split(" ", 1)
            parts = rest.strip().split(" ", 1)
            if len(parts) < 2:
                print_error("用法：/skill-evolve <技能名> <可沉淀的经验>")
                continue
            result = evolve_skill(parts[0], parts[1], rationale="Manual REPL evolution", target="active")
            if result.get("ok"):
                print_info(f"技能 {result.get('skill')} 已进化到版本 {result.get('version')}")
            else:
                print_error(str(result.get("error") or result))
            continue
        if inp.startswith("/skill-create "):
            _, rest = inp.split(" ", 1)
            parts = [part.strip() for part in rest.split("|", 3)]
            if len(parts) < 4 or not all(parts[:4]):
                print_error("用法：/skill-create <名称> | <描述> | <何时使用> | <指令>")
                continue
            result = create_skill(
                name=parts[0],
                description=parts[1],
                when_to_use=parts[2],
                instructions=parts[3],
                target="project",
                context="inline",
                user_invocable=False,
                evidence="Manual REPL skill creation",
            )
            if result.get("ok"):
                print_info(f"已创建技能 {result.get('skill')}：{result.get('file')}")
            else:
                print_error(str(result.get("error") or result))
            continue

        # Skill invocation: /<skill-name> [args]
        if inp.startswith("/"):
            space_idx = inp.find(" ")
            cmd_name = inp[1:space_idx] if space_idx > 0 else inp[1:]
            cmd_args = inp[space_idx + 1:] if space_idx > 0 else ""
            skill = get_skill_by_name(cmd_name)
            if skill and skill.user_invocable:
                print_info(f"正在调用技能：{skill.name}")
                try:
                    if skill.context == "fork":
                        await agent.chat(f'Use the skill tool to invoke "{skill.name}" with args: {cmd_args or "(none)"}')
                    else:
                        result = execute_skill(skill.name, cmd_args)
                        if not result:
                            print_error(f"未知技能：{skill.name}")
                            continue
                        await agent.chat(result["prompt"])
                except Exception as e:
                    if "abort" not in str(e).lower():
                        print_error(str(e))
                continue

        # Normal chat
        try:
            await agent.chat(inp)
        except Exception as e:
            if "abort" not in str(e).lower():
                print_error(str(e))
    await agent.drain_background_skill_tasks()


async def run_one_shot(agent: Agent, prompt: str) -> None:
    await agent.chat(prompt)
    await agent.drain_background_skill_tasks()


def main() -> None:
    """CLI 程序入口：准备运行配置，创建 Agent，并按参数选择一次性执行或交互模式。"""
    # 解析命令行参数，例如 --plan、--resume、--model，以及可选的一次性 prompt。
    args = parse_args()
    _load_env_file()

    if args.help:
        # 自定义帮助文本，展示 Lily 支持的启动参数和 REPL 内置命令。
        print("""
用法：lily [选项] [提示词]

选项：
  --yolo, -y          跳过所有确认提示（bypassPermissions 模式）
  --plan              计划模式：只读，只描述改动不执行
  --accept-edits      自动批准文件编辑，危险 shell 命令仍需确认
  --dont-ask          需要确认的操作一律自动拒绝（用于 CI）
  --thinking          启用扩展思考（仅 Anthropic）
  --model, -m         指定模型（默认：deepseek-chat，或读取 MODEL 环境变量）
  --api-base URL      从命令行或 .env 覆盖 API base URL
  --resume            恢复上一次会话
  --max-cost USD      预估费用超过该值时停止
  --max-turns N       智能体轮数达到 N 后停止
  --help, -h          显示本帮助

REPL 命令：
  /clear              清空对话历史
  /plan               进入计划模式（只读）
  /plan-exit          退出计划模式
  /plans              查看所有任务计划；/plans <文件名> 查看具体计划
  /plans-create       创建计划：/plans-create <任务>
  /cost               显示 token 用量和费用
  /compact            手动压缩对话
  /memory             列出已保存的记忆
  /remember           强制保存记忆：/remember <内容> 或 /remember <名称> | <描述> | <类型> | <内容>
  /forget             删除记忆：/forget <文件名或关键词>
  /skills             列出可用技能
  /skill-stats        显示技能使用与进化统计
  /skill-eval         评估在线技能进化质量
  /extract_now        提取当前待处理的在线技能窗口：/extract_now [提示]
  /skill-feedback     记录反馈：/skill-feedback <技能> <评分> [备注]
  /skill-evolve       进化技能：/skill-evolve <技能> <可沉淀的经验>
  /skill-create       创建技能：/skill-create <名称> | <描述> | <何时使用> | <指令>
  /<技能名>           调用技能（例如 /commit "fix types"）

示例：
  mini-claude "fix the bug in src/app.ts"
  mini-claude --yolo "run all tests and fix failures"
  mini-claude --plan "how would you refactor this?"
  mini-claude --max-cost 0.50 --max-turns 20 "implement feature X"
  MODEL=deepseek-chat APIKEY=sk-xxx API=https://api.deepseek.com/anthropic mini-claude "hello"
  MODEL=gpt-4o OPENAI_API_KEY=sk-xxx OPENAI_BASE_URL=https://aihubmix.com/v1 mini-claude "hello"
  mini-claude --resume
  mini-claude  # 启动交互式 REPL
""")
        sys.exit(0)

    # 将命令行布尔开关统一转换成 Agent 内部使用的权限模式。
    permission_mode = _resolve_permission_mode(args)
    # 模型优先使用命令行参数，其次读取 .env 中的 MODEL，最后回落到默认模型。
    model = args.model or os.environ.get("MODEL") or "deepseek-chat"
    resolved_api_base, resolved_api_key, resolved_use_openai = _resolve_api_config(args.api_base)

    # 没有可用 API key 时无法调用模型，直接提示配置方式并退出。
    if not resolved_api_key:
        print_error(
            "缺少 API key。\n"
            "  可在 .env 中设置 APIKEY（+ 可选 API）作为通用配置，\n"
            "  或使用 ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL，\n"
            "  或使用 OPENAI_API_KEY / OPENAI_BASE_URL。"
        )
        sys.exit(1)

    # 创建主 Agent。OpenAI-compatible 和 Anthropic 原生接口使用不同的 base URL 参数名传入。
    from .agent import Agent

    agent = Agent(
        permission_mode=permission_mode,
        model=model,
        thinking=args.thinking,
        max_cost_usd=args.max_cost,
        max_turns=args.max_turns,
        api_base=resolved_api_base if resolved_use_openai else None,
        anthropic_base_url=resolved_api_base if not resolved_use_openai else None,
        api_key=resolved_api_key,
    )

    # Resume session
    # --resume 会加载最近一次会话，把历史消息恢复到新建的 Agent 中。
    if args.resume:
        session_id = get_latest_session_id()
        if session_id:
            session = load_session(session_id)
            if session:
                agent.restore_session({
                    "anthropicMessages": session.get("anthropicMessages"),
                    "openaiMessages": session.get("openaiMessages"),
                })
            else:
                print_info("未找到可恢复的会话。")
        else:
            print_info("没有找到历史会话。")

    # 如果命令行后面带了普通文本参数，就拼成一次性 prompt；否则进入交互式 REPL。
    prompt = " ".join(args.prompt) if args.prompt else None

    if prompt:
        # One-shot mode
        # 一次性模式：执行完用户 prompt 后进程结束。
        try:
            asyncio.run(run_one_shot(agent, prompt))
        except Exception as e:
            print_error(str(e))
            sys.exit(1)
    else:
        # Interactive REPL
        # 交互模式：启动循环读取用户输入，直到用户退出。
        asyncio.run(run_repl(agent))


if __name__ == "__main__":
    main()
