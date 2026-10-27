#!/usr/bin/env python3
from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from agents.memory import get_memory_dir, _update_memory_index
from agents.paths import plans_dir

logger = logging.getLogger(__name__)

ToolDef = dict  # Anthropic tool schema dict
#权限模式
PermissionMode = str  # "default" | "plan" | "acceptEdits" | "bypassPermissions" | "dontAsk"

READ_TOOLS = {"read_file", "list_files", "grep_search", "compact_context"}
EDIT_TOOLS = {"write_file", "edit_file", "skill_evolve", "skill_create", "save_memory", "delete_memory"}


#并发安全的工具可以并行运行（只读，无副作用）
CONCURRENCY_SAFE_TOOLS = {"read_file", "list_files", "grep_search"}

# 计划模式下仍然暴露给模型的工具。其余写类工具（save_memory、delete_memory、
# skill_create、skill_evolve、run_shell 等）在 check_permission 里必然被拒，
# 继续留在请求里只会诱导模型去调一个注定失败的工具，所以直接不发送。
PLAN_MODE_TOOLS = READ_TOOLS | {
    "write_file", "edit_file",
    "enter_plan_mode", "exit_plan_mode",
    "agent", "tool_search",
    "search_web", "get_web_content",
}


# read_file 默认只返回这么多行。整份读大文件会撞上 _persist_large_result 的 30 KB
# 阈值，模型拿不到内容又去重读，形成自我维持的循环，所以必须分页。
DEFAULT_READ_LIMIT = 400
MAX_READ_LIMIT = 2000


#工具定义
tool_definitions: list[ToolDef] = [
    {
        "name": "read_file",
        "description": "Read a file and return it with line numbers. Large files must be paged through with offset/limit instead of read in one call.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "The path to the file to read"},
                "offset": {"type": "integer", "description": "1-based line to start from. Defaults to 1."},
                "limit": {"type": "integer", "description": f"Max lines to return. Defaults to {DEFAULT_READ_LIMIT}, max {MAX_READ_LIMIT}. Page with offset to read further."},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates the file if it doesn't exist, overwrites if it does.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "The path to the file to write"},
                "content": {"type": "string", "description": "The content to write to the file"},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Edit a file by replacing an exact string match with new content. The old_string must match exactly (including whitespace and indentation).",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "The path to the file to edit"},
                "old_string": {"type": "string", "description": "The exact string to find and replace"},
                "new_string": {"type": "string", "description": "The string to replace it with"},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    {
        "name": "list_files",
        "description": "List files matching a glob pattern. Returns matching file paths.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": 'Glob pattern to match files (e.g., "**/*.ts", "src/**/*")'},
                "path": {"type": "string", "description": "Base directory to search from. Defaults to current directory."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "grep_search",
        "description": "Search for a pattern in files. Returns matching lines with file paths and line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "The regex pattern to search for"},
                "path": {"type": "string", "description": "Directory or file to search in. Defaults to current directory."},
                "include": {"type": "string", "description": 'File glob pattern to include (e.g., "*.ts", "*.py")'},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "run_shell",
        "description": "Execute a shell command and return its output. Use this for running tests, installing packages, git operations, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "timeout": {"type": "number", "description": "Timeout in milliseconds (default: 30000)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "skill",
        "description": "Invoke a registered skill by name. Skills are prompt templates loaded from .lily/skills/. Returns the skill's resolved prompt to follow.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "The name of the skill to invoke"},
                "args": {"type": "string", "description": "Optional arguments to pass to the skill"},
            },
            "required": ["skill_name"],
        },
    },
    {
        "name": "compact_context",
        "description": "Compact the current conversation context into structured session memory when the context is long, tool results are noisy, or a strategy reset is useful. This preserves task progress, current next steps, and tool-use experience, then continues from the folded memory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Brief reason for compacting now, such as long context, many tool calls, repeated failures, or strategy change.",
                },
            },
        },
    },
    {
        "name": "skill_evolve",
        "description": "Persist an explicit reusable user correction or workflow preference into an existing skill. Creates a version snapshot before editing the skill.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "The registered skill name to evolve"},
                "lesson": {"type": "string", "description": "Durable reusable rule to add to the skill"},
                "rationale": {"type": "string", "description": "Why this lesson should affect future similar tasks"},
                "target": {
                    "type": "string",
                    "enum": ["active", "project", "user"],
                    "description": "Which skill file to update. Defaults to active.",
                },
            },
            "required": ["skill_name", "lesson"],
        },
    },
    {
        "name": "skill_create",
        "description": (
            "Create a new reusable skill from explicit durable workflow guidance when no suitable existing skill exists. "
            "The instructions must be an executable workflow: concrete ordered steps, the tools or commands each step uses, "
            "and the expected output. Never write placeholder steps such as 'ask the user for the API key' or 'call the model API' "
            "as if they were executable. If the workflow requires a capability the assistant does not have "
            "(for example calling an external HTTP API or generating images), state that limitation explicitly in the instructions "
            "and tell the user which tool or MCP server must be configured."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Concise reusable skill name"},
                "description": {"type": "string", "description": "One-sentence description of what the skill does and when to use it"},
                "instructions": {
                    "type": "string",
                    "description": (
                        "Required, non-empty SKILL.md body. Must contain concrete ordered steps (what to do, with which tool/file/path, "
                        "and what output is produced). Focus on durable method and constraints, not one-off task content."
                    ),
                },
                "when_to_use": {"type": "string", "description": "Trigger condition for auto-invocation"},
                "target": {
                    "type": "string",
                    "enum": ["project", "user"],
                    "description": "Where to create the skill. Defaults to project.",
                },
                "context": {
                    "type": "string",
                    "enum": ["inline", "fork"],
                    "description": "Skill execution mode. Defaults to inline.",
                },
                "user_invocable": {"type": "boolean", "description": "Whether users can invoke it manually with /<skill>. Defaults to false."},
                "allowed_tools": {"type": "string", "description": "Optional comma-separated allowed tools for fork mode"},
                "evidence": {"type": "string", "description": "Short user-provided evidence showing why this is reusable"},
            },
            "required": ["name", "description", "instructions"],
        },
    },
    {
        "name": "save_memory",
        "description": "Save a durable memory entry into the persistent memory system. Prefer this over writing memory files by hand: it builds the filename and frontmatter and keeps MEMORY.md in sync.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short human-readable title for the memory"},
                "description": {"type": "string", "description": "One-line summary used when deciding whether to recall this memory"},
                "type": {
                    "type": "string",
                    "enum": ["user", "feedback", "project", "reference"],
                    "description": "user = role, preferences, knowledge level; feedback = user corrections (include why and how to apply); project = ongoing work, goals, deadlines, decisions; reference = pointer to an external resource",
                },
                "content": {"type": "string", "description": "The durable fact itself. Exclude ephemeral task details, code patterns and git history."},
            },
            "required": ["name", "description", "type", "content"],
        },
    },
    {
        "name": "delete_memory",
        "description": "Delete a memory entry by filename and refresh the MEMORY.md index. Filenames are listed in the memory index injected into your system prompt.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Memory filename such as user_pnpm.md. Path separators are rejected; MEMORY.md cannot be deleted."},
            },
            "required": ["filename"],
        },
    },
    {
        "name": "enter_plan_mode",
        "description": "Enter plan mode to switch to a read-only planning phase. In plan mode, you can only read files and write to the plan file.",
        "input_schema": {"type": "object", "properties": {}},
        "deferred": True,
    },
    {
        "name": "exit_plan_mode",
        "description": "Exit plan mode after you have finished writing your plan to the plan file.",
        "input_schema": {"type": "object", "properties": {}},
        "deferred": True,
    },
    {
        "name": "agent",
        "description": "Launch a sub-agent to handle a task autonomously. Sub-agents have isolated context and return their result. Types: 'explore' (read-only), 'plan' (read-only, structured planning), 'general' (full tools).",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Short (3-5 word) description of the sub-agent's task"},
                "prompt": {"type": "string", "description": "Detailed task instructions for the sub-agent"},
                "type": {"type": "string", "enum": ["explore", "plan", "general"], "description": "Agent type. Default: general"},
            },
            "required": ["description", "prompt"],
        },
    },
    # ─── 联网工具 ─────────────────────
    {
        "name": "search_web",
        "description": "Search the web for information using DuckDuckGo API. Returns formatted search results with titles, content snippets, and URLs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string"},
                "max_results": {"type": "integer", "description": "Maximum number of results to return (default: 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_web_content",
        "description": "Get and summarize web page content. Returns title and main content excerpt from a given URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Web page URL to fetch content from"},
                "max_length": {"type": "integer", "description": "Maximum content length (default: 2000)", "default": 2000},
            },
            "required": ["url"],
        },
    },
    # ─── 通用 HTTP 请求（调用外部 API） ─────────────────────────
    {
        "name": "http_request",
        "description": (
            "Send an arbitrary HTTP request (GET/POST/PUT/PATCH/DELETE) to an external API. "
            "Supports custom headers, JSON bodies and plain bodies. "
            "Set save_to to write a binary response (e.g. an image) to a file and get the saved path back. "
            "Every call requires user confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
                    "description": (
                        "HTTP method. Always state it explicitly. Use POST for creation or generation "
                        "endpoints that accept a JSON body; GET only for plain reads."
                    ),
                },
                "url": {"type": "string", "description": "Full http:// or https:// URL"},
                "headers": {
                    "type": "object",
                    "description": "Request headers, e.g. {\"Authorization\": \"Bearer ...\"}",
                    "additionalProperties": {"type": "string"},
                },
                "json_body": {"type": "object", "description": "JSON request body (mutually exclusive with body)"},
                "body": {"type": "string", "description": "Raw request body (mutually exclusive with json_body)"},
                "timeout": {"type": "number", "description": "Total timeout in seconds, default 60"},
                "save_to": {
                    "type": "string",
                    "description": "Optional file path. When set, the raw response bytes are written here instead of being returned as text.",
                },
            },
            "required": ["method", "url"],
        },
    },
    # ─── 图片生成（CogView） ───────────────────────────────────
    {
        "name": "generate_image",
        "description": (
            "Generate one image from a text prompt with Zhipu CogView and save it to disk in a single "
            "confirmed call. Reads the API key from the environment, posts to the image endpoint, "
            "downloads the result, and returns the absolute path, byte size and model. "
            "Optimize the user's request into a concrete visual prompt before calling."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Optimized image prompt: subject and action, setting, composition, lighting, style, palette",
                },
                "size": {
                    "type": "string",
                    "description": (
                        "Image size. Default 1024x1024; portrait 768x1344 or 864x1152; "
                        "landscape 1344x768, 1152x864 or 1440x720."
                    ),
                },
                "save_to": {
                    "type": "string",
                    "description": "Optional absolute target path, e.g. C:\\Users\\me\\Desktop\\cat.png. Defaults to the real Desktop.",
                },
                "model": {"type": "string", "description": "CogView model name, default cogview-3-flash"},
            },
            "required": ["prompt"],
        },
    },
    # ─── Tool search (deferred tool loader) ─────────────────────
    {
        "name": "tool_search",
        "description": "Search for available tools by name or keyword. Returns full schema definitions for matching deferred tools so you can use them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Tool name or search keywords"},
            },
            "required": ["query"],
        },
    },
]





#----------------------工具调用----------------------------

def _resolve_tool_path(raw_path: str, *, must_exist: bool = True) -> Path:
    # ~ 必须还原成用户主目录。否则它会按相对路径落到 cwd 下，生成一个名为 "~" 的目录。
    path = Path(raw_path).expanduser()
    if path.exists() or not path.is_absolute():
        return path

    # 写入场景：绝不重映射，写到模型声明的位置（由调用方 mkdir 父目录），
    # 失败就明确报错，避免幽灵路径被静默落到项目根目录。
    if not must_exist:
        return path

    parts = path.parts
    cwd = Path.cwd()
    for i in range(1, len(parts)):
        candidate = cwd.joinpath(*parts[i:])
        if candidate.exists():
            return candidate

    return path


#读取文件并且在读取文件的基础上添加行号
def _read_int(inp: dict, key: str, default: int, maximum: int) -> int:
    """读取分页参数，非法值退回默认并夹在 [1, maximum] 内。"""
    try:
        value = int(inp.get(key) or default)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, maximum))


def _read_file(inp:dict) -> str:
    try:
        path = _resolve_tool_path(inp["file_path"])
        content = path.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")
        total = len(lines)
        start = _read_int(inp, "offset", 1, max(1, total))
        limit = _read_int(inp, "limit", DEFAULT_READ_LIMIT, MAX_READ_LIMIT)
        window = lines[start - 1:start - 1 + limit]
        numbered = "\n".join(f"{i:4d} | {line}" for i, line in enumerate(window, start))
        shown = start - 1 + len(window)
        if shown < total:
            numbered += f"\n\n[{shown}/{total} lines shown. Continue with offset={shown + 1} limit={limit}.]"
        elif start > 1:
            numbered += f"\n\n[{shown}/{total} lines shown. End of file.]"
        return numbered
    except Exception as e:
        return f"Error reading file: {e}"

def _write_file(inp:dict) -> str:
    try:
        path = _resolve_tool_path(inp["file_path"], must_exist=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(inp["content"], encoding="utf-8")
        if str(path).startswith(str(get_memory_dir())) and path.suffix == ".md" and path.name != "MEMORY.md":
            try:
                _update_memory_index()
            except OSError:
                pass
        lines = inp["content"].split("\n")
        line_count = len(lines)
        preview = "\n".join(f"{i + 1:4d} | {l}" for i, l in enumerate(lines[:30]))
        trunc = f"\n  ... ({line_count} lines total)" if line_count > 30 else ""
        return f"Successfully wrote to {inp['file_path']} ({line_count} lines)\n\n{preview}{trunc}"
    except Exception as e:
        return f"Error writing file: {type(e).__name__}: {e} (目标: {inp['file_path']})"

#-------------------------编辑助手，符号规范化+差异化------------------------

#将各种Unicode引号字符统一转换为标准的ASCII直引号。
def _normalize_quotes(s: str) -> str:
    s = re.sub("[\u2018\u2019\u2032]", "'", s)
    s = re.sub('[\u201c\u201d\u2033]', '"', s)
    return s

#查询匹配到的字符串
def _find_actual_string(file_content: str, search_string: str) -> str | None:
    if search_string in file_content:
        return search_string
    norm_search = _normalize_quotes(search_string)
    norm_file = _normalize_quotes(file_content)
    idx = norm_file.find(norm_search)
    if idx != -1:
        return file_content[idx:idx + len(search_string)]
    return None

#生成一个简单的文本差异格式
def _generate_diff(old_content: str, old_string: str, new_string: str) -> str:
    before_change = old_content.split(old_string)[0]
    line_num = before_change.count("\n") + 1
    old_lines = old_string.split("\n")
    new_lines = new_string.split("\n")

    parts = [f"@@ -{line_num},{len(old_lines)} +{line_num},{len(new_lines)} @@"]
    for l in old_lines:
        parts.append(f"- {l}")
    for l in new_lines:
        parts.append(f"+ {l}")
    return "\n".join(parts)

#编辑文件
def _edit_file(inp: dict) -> str:
    try:
        path = _resolve_tool_path(inp["file_path"])
        content = path.read_text(encoding="utf-8", errors="replace")

        actual = _find_actual_string(content, inp["old_string"])
        if not actual:
            return f"Error: old_string not found in {inp['file_path']}"

        occurrences = content.count(inp["old_string"])
        if occurrences > 1:
            return f"Error: old_string found {occurrences} times in {inp['file_path']}. Must be unique."

        new_content = content.replace(actual, inp["new_string"], 1)
        path.write_text(new_content, encoding="utf-8")

        diff = _generate_diff(content, actual, inp["new_string"])

        quote_note = " (matched via quote normalization)" if actual != inp["old_string"] else ""

        return f"Successfully edited {inp['file_path']}{quote_note}\n\n{diff}"
    except Exception as e:
        return f"Error editing file: {e}"


def _list_files(inp: dict) -> str:
    try:
        base = _resolve_tool_path(inp.get("path") or ".")
        pattern = inp["pattern"]
        files = []
        for p in base.glob(pattern):
            if p.is_file():
                rel = str(p.relative_to(base) if base != Path(".") else p)

                if "node_modules" in rel or ".git" in rel.split(os.sep):
                    continue
                files.append(rel)
                if len(files) >= 200:
                    break
        if not files:
            return "No files found matching the pattern."
        result = "\n".join(files[:200])

        if len(files) > 200:
            result += f"\n...and {len(files) - 200} more files are found ..."
        return result
    except Exception as e:
        return f"Error listing files: {e}"


def _grep_search(inp: dict) -> str:
    pattern = inp["pattern"]
    path = str(_resolve_tool_path(inp.get("path") or "."))
    include = inp.get("include")

    if sys.platform != "win32":
        try:
            args = ["grep", "--line-number", "--color=never", "-r"]
            if include:
                args.append(f"--include={include}")
            args.extend(["--", pattern, path])

            result = subprocess.run(
                args, capture_output=True, text=True, timeout=10
            )
            if result.returncode == 1:
                return "No matches found."
            if result.returncode == 0:
                lines = [l for l in result.stdout.split("\n") if l]
                output = "\n".join(lines[:100])
                if len(lines) >100:
                    output += f"\n... and {len(lines) - 100} more matches"
                return output
        except (subprocess.TimeoutExpired, OSError):
            pass

    return _grep_python(pattern, path, include)


def _grep_python(pattern: str, directory: str, include: str | None) -> str:
    regex = re.compile(pattern)
    include_pattern = include
    matches: list[str] = []

    def walk(d: str) -> None:
        if len(matches) >= 200:
            return
        try:
            entries = os.listdir(d)
        except OSError:
            return
        for name in entries:
            if name.startswith(".") or name == "node_modules":
                continue
            full = os.path.join(d, name)
            if os.path.isdir(full):
                walk(full)
                continue
            if include_pattern and not fnmatch.fnmatch(name, include_pattern):
                continue
            try:
                text = Path(full).read_text(errors="replace", encoding="utf-8")
                for i, line in enumerate(text.split("\n")):
                    if regex.search(line):
                        matches.append(f"{full}:{i+1}:{line}")
                        if len(matches) >= 200:
                            return
            except OSError:
                pass

    walk(directory)
    if not matches:
        return "No matches found."
    output = "\n".join(matches[:100])
    if len(matches) > 100:
        output += f"\n... and {len(matches) - 100} more matches"
    return output



#--------截断过长的工具调用结果

MAX_RESULT_CHARS = 50000


def _truncate_result(result:str) -> str:
    if len(result) <= MAX_RESULT_CHARS:
        return result
    keep_each = (MAX_RESULT_CHARS - 60) // 2
    return (
        result[:keep_each]
        +f"\n\n[... truncated {len(result) - keep_each * 2} chars ...]\n\n"
        +result[-keep_each:]
    )






#----------延迟工具激活---------------------------
_activated_tools: set[str] = set()



def reset_activated_tools() -> None:
    _activated_tools.clear()

def get_active_tool_definitions(all_tools: list[ToolDef] | None = None) -> list[ToolDef]:
    tools = all_tools if all_tools is not None else tool_definitions
    return [
        {k: v for k, v in t.items() if k != "deferred"}
        for t in tools
        if not t.get("deferred") or t["name"] in _activated_tools
    ]

def get_deferred_tool_names(all_tools: list[ToolDef] | None = None) -> list[str]:
    tools = all_tools if all_tools is not None else tool_definitions
    return [t["name"] for t in tools if t.get("deferred") and t["name"] not in _activated_tools]

def get_plan_mode_tool_definitions(all_tools: list[ToolDef] | None = None) -> list[ToolDef]:
    """计划模式下发给模型的工具列表：只保留只读工具和计划文件写入。

    enter_plan_mode / exit_plan_mode 平时是 deferred，不进请求。但一旦已经处于计划模式，
    系统提示词会强制要求模型"完成后调用 exit_plan_mode"，此时必须把它发出去：否则模型
    手里根本没有这个工具，只能退而调用别的写工具，被拒后向用户宣称已经保存。
    """
    tools = all_tools if all_tools is not None else tool_definitions
    visible = {t["name"] for t in get_active_tool_definitions(tools)}
    forced = {"enter_plan_mode", "exit_plan_mode"}
    return [
        {k: v for k, v in t.items() if k != "deferred"}
        for t in tools
        if t["name"] in PLAN_MODE_TOOLS and (t["name"] in visible or t["name"] in forced)
    ]

#执行shell命令
def _run_shell(inp: dict) -> str:
    try:
        timeout_ms = inp.get("timeout", 30000)
        timeout_s = timeout_ms / 1000
        result = subprocess.run(
            inp["command"],
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        output = result.stdout or ""
        if result.returncode != 0:
            stderr = f"\nStderr: {result.stderr}" if result.stderr else ""
            stdout = f"\nStdout: {result.stdout}" if result.stdout else ""
            return f"Command failed (exit code {result.returncode}){stdout}{stderr}"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {inp.get('timeout', 30000)}ms"
    except Exception as e:
        return f"Error: {e}"


#危险命令检测模式列表

DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s"),
    re.compile(r"\bgit\s+(push|reset|clean|checkout\s+\.)"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s"),
    re.compile(r">\s*/dev/"),
    re.compile(r"\bkill\b"),
    re.compile(r"\bpkill\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bshutdown\b"),
    re.compile(r"\bdel\s", re.IGNORECASE),
    re.compile(r"\brmdir\s", re.IGNORECASE),
    re.compile(r"\bformat\s", re.IGNORECASE),
    re.compile(r"\btaskkill\s", re.IGNORECASE),
    re.compile(r"\bRemove-Item\s", re.IGNORECASE),
    re.compile(r"\bStop-Process\s", re.IGNORECASE),
]
def is_dangerous(command: str) -> bool:
    return any(p.search(command) for p in DANGEROUS_PATTERNS)

#权限规则
def _parse_rule(rule: str) -> dict:
    m = re.match(r"^([a-z_]+)\((.+)\)$", rule)
    if m:
        return {"tool": m.group(1), "pattern": m.group(2)}
    return {"tool": rule, "pattern": None}

def _load_settings(file_path: Path) -> dict | None:
    if not file_path.exists():
        return None
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("failed to load settings file %s: %s", file_path, e)
        return None

_cached_rules: dict | None = None



def load_permission_rules() -> dict:
    global _cached_rules
    if _cached_rules is not None:
        return _cached_rules

    allow: list[dict] = []
    deny: list[dict] = []

    user_settings = _load_settings(Path.home() / ".lily" / "settings.json")
    project_settings = _load_settings(Path.cwd() / ".lily" / "settings.json")

    for settings in [user_settings, project_settings]:
        if not settings or "permissions" not in settings:
            continue
        perms = settings["permissions"]
        for r in perms.get("allow", []):
            allow.append(_parse_rule(r))
        for r in perms.get("deny", []):
            deny.append(_parse_rule(r))

    _cached_rules = {"allow": allow, "deny": deny}
    return _cached_rules


def _matches_rule(rule: dict, tool_name: str, inp: dict) -> bool:
    if rule["tool"] != tool_name:
        return False
    if rule["pattern"] is None:
        return True

    value = ""
    if tool_name == "run_shell":
        value = inp.get("command", "")
    elif "file_path" in inp:
        value = inp["file_path"]
    else:
        return True

    pattern = rule["pattern"]
    if pattern.endswith("*"):
        return value.startswith(pattern[:-1])
    return value == pattern


def _check_permission_rules(tool_name: str, inp: dict) -> str | None:
    rules = load_permission_rules()
    for rule in rules["deny"]:
        if _matches_rule(rule, tool_name, inp):
            return "deny"
    for rule in rules["allow"]:
        if _matches_rule(rule, tool_name, inp):
            return "allow"
    return None


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path


def _same_path(left: object, right: object) -> bool:
    """比较两个路径是否指向同一文件，容忍相对/绝对写法的差异。"""
    if not left or not right:
        return False
    try:
        return _absolute(Path(str(left))).resolve() == _absolute(Path(str(right))).resolve()
    except (OSError, ValueError, TypeError):
        return str(left) == str(right)


def _is_plan_file_location(raw_path: object) -> bool:
    """判断目标是否直接位于计划目录，这是计划模式下唯一允许新建文件的位置。"""
    if not raw_path:
        return False
    try:
        target = _absolute(Path(str(raw_path)))
        return target.parent.resolve() == plans_dir().resolve()
    except (OSError, ValueError, TypeError):
        return False


def check_permission(
    tool_name: str,
    inp: dict,
    mode: str = "default",
    plan_file_path: str | None = None,
) -> dict:
    """Returns {"action": "allow"|"deny"|"confirm", "message": ...}"""
    if mode == "bypassPermissions":
        return {"action": "allow"}

    rule_result = _check_permission_rules(tool_name, inp)
    if rule_result == "deny":
        return {"action": "deny", "message": f"被 {tool_name} 的权限规则拒绝"}
    if rule_result == "allow":
        return {"action": "allow"}

    if tool_name in READ_TOOLS:
        return {"action": "allow"}

    if mode == "plan":
        if tool_name in EDIT_TOOLS:
            file_path = inp.get("file_path") or inp.get("path")
            if plan_file_path and _same_path(file_path, plan_file_path):
                return {"action": "allow"}
            # 普通 /plan 不再预分配计划文件名，此时允许模型直接在计划目录里
            # 新建一份按任务命名的计划；写入成功后会被登记为当前计划文件。
            if plan_file_path is None and tool_name in {"write_file", "edit_file"} and _is_plan_file_location(file_path):
                return {"action": "allow"}
            # 光说"被阻止"会让模型直接宣称保存成功，这里必须给出下一步该做什么。
            target = plan_file_path or str(plans_dir() / "<任务名>.md")
            return {
                "action": "deny",
                "message": (
                    f"计划模式是只读的，{tool_name} 未执行，内容没有保存。"
                    f"请改用 write_file 把内容写入 {target}（这是唯一可写文件），"
                    f"完成后再调用 exit_plan_mode。不要向用户声称已保存。"
                ),
            }
        if tool_name in ("run_shell", "http_request", "generate_image"):
            return {"action": "deny", "message": f"计划模式下禁止执行 {tool_name}（只读分析阶段不发起外部请求、不写文件）"}

    if tool_name in ("enter_plan_mode", "exit_plan_mode"):
        return {"action": "allow"}

    if mode == "acceptEdits" and tool_name in EDIT_TOOLS:
        return {"action": "allow"}

    needs_confirm = False
    confirm_message = ""

    if tool_name == "run_shell" and is_dangerous(inp.get("command", "")):
        needs_confirm = True
        confirm_message = inp.get("command", "")
    elif tool_name == "write_file" and not _resolve_tool_path(inp.get("file_path", ""), must_exist=False).exists():
        needs_confirm = True
        confirm_message = f"写入新文件：{inp.get('file_path', '')}"
    elif tool_name == "edit_file" and not _resolve_tool_path(inp.get("file_path", "")).exists():
        needs_confirm = True
        confirm_message = f"编辑不存在的文件：{inp.get('file_path', '')}"
    elif tool_name == "skill_evolve":
        needs_confirm = True
        confirm_message = f"进化技能：{inp.get('skill_name', '')}"
    elif tool_name == "skill_create":
        needs_confirm = True
        confirm_message = f"创建技能：{inp.get('name', '')}"
    elif tool_name == "http_request":
        # 每条外部请求都可能把数据（含密钥）发出去，必须逐次确认。
        needs_confirm = True
        confirm_message = f"外部请求：{str(inp.get('method') or 'GET').upper()} {inp.get('url', '')}"
    elif tool_name == "generate_image":
        # 一次确认覆盖"生成 + 下载落盘"，避免同一任务被拆成两次授权。
        needs_confirm = True
        confirm_message = f"生成图片（CogView）：{str(inp.get('prompt') or '')[:60]}"

    if needs_confirm:
        if mode == "dontAsk":
            return {"action": "deny", "message": f"自动拒绝（dontAsk 模式）：{confirm_message}"}
        return {"action": "confirm", "message": confirm_message}

    return {"action": "allow"}






# CogView 图片生成：一次工具调用完成"POST 生成 → GET 下载落盘"，
# key 只从环境变量读取，不进入模型上下文。
COGVIEW_SIZES = (
    "1024x1024",
    "768x1344", "864x1152",
    "1344x768", "1152x864", "1440x720", "720x1440",
)
_COGVIEW_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/images/generations"


def _image_slug(prompt: str) -> str:
    text = str(prompt or "")
    words = re.findall(r"[A-Za-z0-9]+", text)[:4]
    if words:
        return "-".join(w.lower() for w in words)[:40]
    # 中文提示词没有 ASCII 词；不回退按汉字截取的话会全部塌缩成 image，互相覆盖。
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]+", text))[:8]
    return cjk or "image"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 100):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem}-{os.getpid()}{path.suffix}")


def _resolve_image_key() -> str:
    # 正常情况下 main.py 启动时已加载 .env；这里再兜底一次，避免直接调用工具时拿不到 key。
    key = (os.environ.get("OPENAI_API_KEY") or os.environ.get("BIGMODEL_API_KEY") or "").strip()
    if key:
        return key
    try:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True), override=False)
    except Exception as e:
        logger.warning("failed to load dotenv for image key: %s", e)
        return ""
    return (os.environ.get("OPENAI_API_KEY") or os.environ.get("BIGMODEL_API_KEY") or "").strip()


def _sniff_image_ext(path: str) -> str:
    # CogView 的 CDN 会返回 .png 结尾的 URL，但实际字节可能是 JPEG，落盘后按真实格式纠正。
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
    except OSError:
        return ""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:3] == b"GIF":
        return "gif"
    return ""


async def _generate_image(inp: dict) -> str:
    from .online_tools import http_request

    prompt = str(inp.get("prompt") or "").strip()
    if not prompt:
        return "Error: generate_image 需要 prompt（优化后的提示词）"
    model = str(inp.get("model") or "cogview-3-flash").strip()
    size = str(inp.get("size") or "1024x1024").strip()
    if size not in COGVIEW_SIZES:
        return f"Error: 不支持的 size {size!r}。可选：{', '.join(COGVIEW_SIZES)}"

    api_key = _resolve_image_key()
    if not api_key:
        return "Error: 未找到 OPENAI_API_KEY，无法调用 CogView。请在 .env 中配置后重启。"

    save_to = str(inp.get("save_to") or "").strip()
    auto_path = not save_to
    if save_to:
        bad = next((c for c in '<>"|?*' if c in save_to), None)
        if bad:
            return (
                f"Error: save_to 含 Windows 非法字符 {bad!r}，无法创建文件。"
                "请改用真实绝对路径或 ~/Desktop/<name>.png"
            )
        try:
            # 目标文件是本次新建的，不能要求它已经存在。
            save_to = str(_resolve_tool_path(save_to, must_exist=False).resolve())
        except Exception as e:
            return f"Error: invalid save_to path: {e}"
    else:
        from .prompt import desktop_path

        save_to = str(desktop_path() / f"{_image_slug(prompt)}.png")

    payload: dict = {"model": model, "prompt": prompt, "size": size}
    size_sent = True
    last_error = ""
    for attempt in range(2):
        result = await http_request(
            method="POST",
            url=_COGVIEW_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json_body=payload,
            timeout=180,
        )
        status_line, _, body = result.partition("\n")
        if status_line.startswith("HTTP 200"):
            try:
                image_url = json.loads(body)["data"][0]["url"]
            except Exception:
                return f"Error: 无法解析 CogView 响应：{_truncate_result(body)}"
            break

        last_error = result.strip()
        # 4xx 是参数或鉴权问题，盲目重试只会浪费时间；429 和 5xx 才有重试价值。
        if attempt == 0 and (status_line.startswith("HTTP 429") or status_line.startswith("HTTP 5")):
            continue
        # 部分模型不接受 size，去掉该字段再试一次。
        if attempt == 0 and size_sent and status_line.startswith("HTTP 4"):
            size_sent = False
            payload.pop("size", None)
            continue
        return f"Error: CogView 返回 {last_error}"

    # 统一先落到 .part 暂存名，再按真实字节格式定后缀并避开已有文件，任何情况下都不覆盖旧图。
    staging = str(_unique_path(Path(save_to).with_name(f"{Path(save_to).stem}.part")))
    download = await http_request(method="GET", url=image_url, timeout=180, save_to=staging)
    if "Saved" not in download:
        return f"Error: 图片已生成但下载失败：{download.strip()}\nURL: {image_url}"

    actual = _sniff_image_ext(staging)
    final = _unique_path(Path(staging).with_suffix(f".{actual or 'png'}"))
    try:
        Path(staging).replace(final)
        save_to = str(final)
    except OSError:
        save_to = staging
    try:
        byte_size = os.path.getsize(save_to)
    except OSError:
        byte_size = 0

    return (
        f"Saved {byte_size} bytes to {save_to}\n"
        f"Model: {model}\n"
        f"Size: {payload.get('size', 'default')}\n"
        f"Format: {actual or 'unknown'}\n"
        f"Prompt: {prompt}"
    )


#----------------执行工具调用-----------------------
# 'agent' 和 'skill' 这两个工具在 agent.py 中处理，以避免循环依赖。"

async def execute_tool(
    name: str, inp: dict, read_file_state: dict[str, float] | None = None,
    all_tools: list[ToolDef] | None = None,
) -> str:
    if name == "read_file":
        result = _read_file(inp)
        if read_file_state is not None and not result.startswith("Error"):
            abs_path = str(_resolve_tool_path(inp["file_path"]).resolve())
            try:
                read_file_state[abs_path] =  os.path.getmtime(abs_path)
            except OSError:
                pass
        return result

    if name in ("write_file", "edit_file") and read_file_state is not None:
        abs_path = str(_resolve_tool_path(inp["file_path"], must_exist=(name == "edit_file")).resolve())
        if os.path.exists(abs_path):
            if abs_path not in read_file_state:
                verb = "writing" if name == "write_file" else "editing"
                return f"Error: You must read this file before {verb}. Use read_file first to see its current contents."
            if os.path.getmtime(abs_path) != read_file_state[abs_path]:
                verb = "writing" if name == "write_file" else "editing"
                return f"Warning: {inp['file_path']} was modified externally since your last read. Please read_file again before {verb}."

    #搜索和激活延迟加载的工具。
    if name == "tool_search":
        query = (inp.get("query") or "").lower()
        # all_tools 里包含 MCP 工具，否则 MCP 的延迟工具永远搜不到。
        pool = all_tools if all_tools is not None else tool_definitions
        deferred = [t for t in pool if t.get("deferred")]
        matches = [
            t for t in deferred
            if query in t["name"].lower() or query in (t.get("description") or "").lower()
        ]
        if not matches:
            return "No matching deferred tools found."

        for m in matches:
            _activated_tools.add(m["name"])

        return json.dumps(
            [{"name": t["name"], "description": t.get("description", ""), "input_schema": t["input_schema"]} for t in
             matches],
            indent=2,
        )
    if name == "skill_evolve":
        from .skills import evolve_skill

        result = evolve_skill(
            skill_name=inp.get("skill_name", ""),
            lesson=inp.get("lesson", ""),
            rationale=inp.get("rationale", ""),
            target=inp.get("target", "active"),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    if name == "skill_create":
        from .skills import create_skill

        result = create_skill(
            name=inp.get("name", ""),
            description=inp.get("description", ""),
            instructions=inp.get("instructions", ""),
            when_to_use=inp.get("when_to_use", "") or inp.get("when-to-use", ""),
            target=inp.get("target", "project"),
            context=inp.get("context", "inline"),
            user_invocable=bool(inp.get("user_invocable", False)),
            allowed_tools=inp.get("allowed_tools"),
            evidence=inp.get("evidence", ""),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    # 联网工具处理
    if name == "search_web":
        from .online_tools import search_web
        query = inp.get("query", "").strip()
        max_results = inp.get("max_results", 5)
        if not query:
            return "Error: query is required for web search"
        try:
            result = await search_web(query, max_results)
            return result
        except Exception as e:
            return f"Error searching web: {str(e)}"

    if name == "get_web_content":
        from .online_tools import get_web_content
        url = inp.get("url", "").strip()
        max_length = inp.get("max_length", 2000)
        if not url:
            return "Error: url is required for getting web content"
        try:
            # 运行异步函数
            result = await get_web_content(url, max_length)
            return result
        except Exception as e:
            return f"Error getting web content: {str(e)}"

    if name == "generate_image":
        try:
            return await _generate_image(inp)
        except Exception as e:
            return f"Error in generate_image: {e}"

    if name == "http_request":
        from .online_tools import http_request
        url = (inp.get("url") or "").strip()
        if not url:
            return "Error: url is required for http_request"
        # 不静默退回 GET：模型漏填 method 时若默认成 GET，会把生成类接口调成"方法不支持"。
        method = (inp.get("method") or "").strip().upper()
        if not method:
            return (
                "Error: http_request 必须显式指定 method（GET/POST/PUT/PATCH/DELETE/HEAD）。"
                "创建或生成类接口需用 POST。"
            )
        save_to = inp.get("save_to")
        if save_to:
            bad = next((c for c in '<>"|?*' if c in str(save_to)), None)
            if bad:
                from .prompt import desktop_path

                return (
                    f"Error: save_to 含 Windows 非法字符 {bad!r}，无法创建文件。"
                    f"请改用真实绝对路径，例如 {desktop_path()}\\image.png；"
                    "不要写 <user>、%USERPROFILE% 之类的占位符，也不要传相对路径。"
                )
            # save_to 是本次调用要新建的文件，不能要求它已经存在。
            try:
                save_to = str(_resolve_tool_path(save_to, must_exist=False).resolve())
            except Exception as e:
                return f"Error: invalid save_to path: {e}"
        try:
            return await http_request(
                method=method,
                url=url,
                headers=inp.get("headers"),
                json_body=inp.get("json_body"),
                body=inp.get("body"),
                timeout=inp.get("timeout", 60),
                save_to=save_to,
            )
        except Exception as e:
            return f"Error in http_request: {e}"

    if name == "save_memory":
        from .memory import save_memory

        mem_type = (inp.get("type") or "").strip()
        mem_name = (inp.get("name") or "").strip()
        content = (inp.get("content") or "").strip()
        if not mem_name or not content:
            return "Error: name and content are required"
        try:
            filename = save_memory(mem_name, (inp.get("description") or "").strip(), mem_type, content)
        except ValueError as e:
            return f"Error: {e}"
        return f"Successfully saved memory: {filename}"

    if name == "delete_memory":
        from .memory import delete_memory

        filename = (inp.get("filename") or "").strip()
        if not filename:
            return "Error: filename is required"
        if filename == "MEMORY.md":
            return "Error: MEMORY.md is the generated index and cannot be deleted"
        if delete_memory(filename):
            return f"Successfully deleted memory: {filename}"
        return f"Error: memory file not found: {filename}"

    handlers: dict = {
        "write_file": _write_file,
        "edit_file": _edit_file,
        "list_files": _list_files,
        "grep_search": _grep_search,
        "run_shell": _run_shell,
    }
    handler = handlers.get(name)

    if not handler:
        return f"Unknown tool: {name}"

    result = handler(inp)

    # 更新时间
    if name in ("write_file", "edit_file") and read_file_state is not None and not result.startswith("Error"):
        abs_path = str(_resolve_tool_path(inp["file_path"], must_exist=False).resolve())
        try:
            read_file_state[abs_path] = os.path.getmtime(abs_path)
        except OSError:
            pass

    return result



def reset_permission_cache() -> None:
    global _cached_rules
    _cached_rules = None



