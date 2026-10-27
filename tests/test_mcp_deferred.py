import asyncio

from agents.mcp_client import McpManager
from agents.prompt import build_system_prompt
from agents.tools import (
    execute_tool,
    get_active_tool_definitions,
    reset_activated_tools,
)


def _manager_with_tools():
    manager = McpManager()
    manager._tools = [
        {"serverName": "playwright", "name": "browser_click", "description": "Click an element", "inputSchema": {}},
        {"serverName": "context7", "name": "resolve_library", "description": "Resolve a library id", "inputSchema": {}},
    ]
    return manager


def test_mcp_tool_definitions_are_deferred(isolated_env):
    """MCP 工具必须标 deferred，否则每轮请求都会把它们全量塞进去。"""
    defs = _manager_with_tools().get_tool_definitions()
    assert all(d["deferred"] for d in defs)
    assert [d["name"] for d in defs] == ["mcp__playwright__browser_click", "mcp__context7__resolve_library"]


def test_deferred_mcp_tools_are_not_sent_until_activated(isolated_env):
    reset_activated_tools()
    defs = _manager_with_tools().get_tool_definitions()
    assert get_active_tool_definitions(defs) == []


def test_tool_search_finds_mcp_tools(isolated_env):
    """tool_search 必须能搜到 MCP 工具，否则延迟后模型无法发现它们。"""
    reset_activated_tools()
    defs = _manager_with_tools().get_tool_definitions()
    raw = asyncio.run(execute_tool("tool_search", {"query": "playwright"}, None, defs))
    assert "mcp__playwright__browser_click" in raw

    activated = get_active_tool_definitions(defs)
    assert [d["name"] for d in activated] == ["mcp__playwright__browser_click"]


def test_system_prompt_lists_mcp_tools_as_deferred(isolated_env):
    reset_activated_tools()
    defs = _manager_with_tools().get_tool_definitions()
    prompt = build_system_prompt(defs)
    assert "mcp__playwright__browser_click" in prompt
