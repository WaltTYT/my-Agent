from __future__ import annotations

import asyncio

from agents import online_tools, prompt as prompt_module, tools


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


def test_search_web_returns_network_error_details():
    class BrokenSession:
        closed = False

        def get(self, url, **kwargs):
            raise OSError("network is unreachable")

    tool = online_tools.OnlineSearchTool()
    tool.session = BrokenSession()

    result = asyncio.run(tool.search_web("current news"))

    assert "OSError" in result
    assert "network is unreachable" in result
    assert "不要根据此错误推断搜索结果" in result


def test_search_web_reports_http_failure_status():
    session = FakeSession(FakeResponse(status=503))
    tool = online_tools.OnlineSearchTool()
    tool.session = session

    result = asyncio.run(tool.search_web("current news"))

    assert "HTTP 503" in result
    assert "不要根据此错误推断搜索结果" in result


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


# ─── 通用 HTTP 请求 ─────────────────────────────────────────


class FakeRawResponse:
    def __init__(self, body: bytes, *, status: int = 200, content_type: str = "application/json"):
        self._body = body
        self.status = status
        self.headers = {"Content-Type": content_type}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def read(self):
        return self._body


class FakeRequestSession:
    closed = False

    def __init__(self, response):
        self.response = response
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.response


def _http_tool(response):
    tool = online_tools.OnlineSearchTool()
    tool.session = FakeRequestSession(response)
    return tool


def test_http_request_returns_status_and_body():
    tool = _http_tool(FakeRawResponse(b'{"data": [{"url": "https://img.example/1.png"}]}'))

    result = asyncio.run(tool.http_request(
        "post",
        "https://open.bigmodel.cn/api/paas/v4/images/generations",
        headers={"Authorization": "Bearer sk-test"},
        json_body={"model": "cogview-3-flash", "prompt": "a cat"},
    ))

    assert "HTTP 200" in result
    assert "https://img.example/1.png" in result
    method, url, kwargs = tool.session.requests[0]
    assert method == "POST"
    assert kwargs["json"] == {"model": "cogview-3-flash", "prompt": "a cat"}
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"


def test_http_request_saves_binary_response(tmp_path):
    target = tmp_path / "out" / "cat.png"
    tool = _http_tool(FakeRawResponse(b"\x89PNG-bytes", content_type="image/png"))

    result = asyncio.run(tool.http_request("GET", "https://img.example/1.png", save_to=str(target)))

    assert target.read_bytes() == b"\x89PNG-bytes"
    assert "Saved 10 bytes" in result
    assert "HTTP 200" in result


def test_http_request_rejects_bad_input():
    tool = _http_tool(FakeRawResponse(b"unused"))

    assert "http" in asyncio.run(tool.http_request("GET", "file:///etc/passwd"))
    assert "不支持的请求方法" in asyncio.run(tool.http_request("TRACE", "https://a.com"))
    assert "二选一" in asyncio.run(tool.http_request(
        "POST", "https://a.com", json_body={"a": 1}, body="raw"
    ))
    assert tool.session.requests == []


def test_execute_tool_dispatches_http_request(monkeypatch):
    captured = {}

    async def fake_http_request(**kwargs):
        captured.update(kwargs)
        return "HTTP 200\nok"

    monkeypatch.setattr(online_tools, "http_request", fake_http_request)

    result = asyncio.run(tools.execute_tool(
        "http_request",
        {"method": "POST", "url": "https://a.com", "json_body": {"q": 1}},
    ))

    assert result.startswith("HTTP 200")
    assert captured["method"] == "POST"
    assert captured["json_body"] == {"q": 1}
    assert captured["save_to"] is None


def test_save_to_rejects_placeholder_path(monkeypatch):
    """模型照抄 `<user>` 占位符时必须明确报错，而不是抛 WinError 123。"""
    captured = {}

    async def fake_http_request(**kwargs):
        captured.update(kwargs)
        return "should not run"

    monkeypatch.setattr(online_tools, "http_request", fake_http_request)

    result = asyncio.run(tools.execute_tool(
        "http_request",
        {"method": "GET", "url": "https://img.example/1.png", "save_to": r"C:\Users\<user>\Desktop\cat.png"},
    ))

    assert "非法字符" in result
    assert "占位符" in result
    assert captured == {}


def test_system_prompt_lists_desktop(monkeypatch, tmp_path):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    prompt = prompt_module.build_system_prompt()

    assert f"Desktop: {desktop}" in prompt


def test_save_to_expands_home(monkeypatch, tmp_path):
    """~ 必须指向用户主目录，不能按 cwd 解析成项目下的 "~" 目录。"""
    captured = {}

    async def fake_http_request(**kwargs):
        captured.update(kwargs)
        return "HTTP 200\nok"

    monkeypatch.setattr(online_tools, "http_request", fake_http_request)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    asyncio.run(tools.execute_tool(
        "http_request",
        {"method": "GET", "url": "https://img.example/1.png", "save_to": "~/Desktop/cat.png"},
    ))

    assert captured["save_to"] == str(tmp_path / "Desktop" / "cat.png")


def test_http_request_requires_explicit_method():
    """漏填 method 不能静默退化成 GET，否则生成类接口会被调成方法不支持。"""
    import asyncio

    from agents import tools

    result = asyncio.run(tools.execute_tool(
        "http_request", {"url": "https://open.bigmodel.cn/api/paas/v4/images/generations"}
    ))

    assert "method" in result
    assert "POST" in result
