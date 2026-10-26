from __future__ import annotations

import asyncio

from agents import online_tools, tools


class FakeResponse:
    def __init__(self, *, payload=None, body="", status=200):
        self.payload = payload or {}
        self.body = body
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self):
        return self.payload

    async def text(self):
        return self.body


class FakeSession:
    closed = False

    def __init__(self, response):
        self.response = response
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.response


def test_search_web_formats_instant_answer_and_topics():
    session = FakeSession(FakeResponse(payload={
        "AbstractText": "Python is a programming language.",
        "Heading": "Python",
        "AbstractURL": "https://python.org/",
        "RelatedTopics": [
            {"Text": "Python documentation", "FirstURL": "https://docs.python.org/"},
        ],
    }))
    tool = online_tools.OnlineSearchTool()
    tool.session = session

    result = asyncio.run(tool.search_web("python language", max_results=2))

    assert "Python is a programming language." in result
    assert "https://python.org/" in result
    assert "Python documentation" in result
    assert "https://api.duckduckgo.com/" in session.requests[0][0]
    assert "python%20language" in session.requests[0][0]


def test_get_web_content_extracts_page_text_and_strips_script():
    session = FakeSession(FakeResponse(body=(
        "<html><head><title>Example</title><style>.x { color: red }</style></head>"
        "<body><main>Hello web</main><script>secret()</script></body></html>"
    )))
    tool = online_tools.OnlineSearchTool()
    tool.session = session

    result = asyncio.run(tool.get_web_content("https://example.com", max_length=100))

    assert "Example" in result
    assert "Hello web" in result
    assert "secret" not in result
    assert session.requests[0][1]["headers"]["User-Agent"]


def test_get_web_content_rejects_invalid_url_without_request():
    session = FakeSession(FakeResponse(body="unused"))
    tool = online_tools.OnlineSearchTool()
    tool.session = session

    result = asyncio.run(tool.get_web_content("not-a-url"))

    assert result == "无效的URL格式"
    assert session.requests == []


def test_execute_tool_dispatches_online_tools_as_async_calls(monkeypatch):
    async def fake_search(query, max_results):
        return f"search:{query}:{max_results}"

    async def fake_content(url, max_length):
        return f"content:{url}:{max_length}"

    monkeypatch.setattr(online_tools, "search_web", fake_search)
    monkeypatch.setattr(online_tools, "get_web_content", fake_content)

    search = asyncio.run(tools.execute_tool(
        "search_web", {"query": "python", "max_results": 3}
    ))
    content = asyncio.run(tools.execute_tool(
        "get_web_content", {"url": "https://example.com", "max_length": 50}
    ))

    assert search == "search:python:3"
    assert content == "content:https://example.com:50"
