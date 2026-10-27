from __future__ import annotations

import asyncio
import base64

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


BING_RSS = (
    "<rss><channel>"
    "<item><title>2026 Asian Games table tennis</title>"
    "<link>https://news.example/asian-games</link>"
    "<description>&lt;b&gt;Lin Shidong&lt;/b&gt; won the men singles title</description>"
    "</item>"
    "</channel></rss>"
)


def _bing_redirect(target: str) -> str:
    token = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    return f"https://www.bing.com/ck/a?!&&p=abc&u=a1{token}&ntab=1"


BING_HTML = (
    "<html><body><ol id=\"b_results\">"
    "<li class=\"b_algo\">"
    f"<h2><a href=\"{_bing_redirect('https://news.example/asian-games')}\">"
    "2026年亚运会乒乓球&nbsp;男单决赛</a></h2>"
    "<div class=\"b_caption\"><p>林诗栋夺得<em>男单</em>冠军，"
    "4:0战胜王楚钦</p></div></li>"
    "<li class=\"b_algo\">"
    "<h2><a href=\"https://zh.wikipedia.org/wiki/x\">亚运会乒乓球比赛</a></h2>"
    "<p>第20届亚洲运动会乒乓球项目</p></li>"
    "</ol></body></html>"
)


def test_search_web_parses_bing_serp_and_decodes_redirect_links():
    session = FakeSession(FakeResponse(body=BING_HTML))
    tool = online_tools.OnlineSearchTool()
    tool.session = session

    result = asyncio.run(tool.search_web("2026 亚运会 乒乓球", max_results=3))

    assert "https://news.example/asian-games" in result
    assert "/ck/a" not in result
    assert "林诗栋夺得 男单 冠军" in result
    assert "<em>" not in result
    assert "https://zh.wikipedia.org/wiki/x" in result
    assert "bing.com" in session.requests[0][0]
    assert "format=rss" not in session.requests[0][0]


def test_search_web_falls_back_to_bing_rss_when_serp_empty():
    session = FakeSession(FakeResponse(body=BING_RSS))
    tool = online_tools.OnlineSearchTool()
    tool.session = session

    result = asyncio.run(tool.search_web("2026 亚运会 乒乓球", max_results=3))

    assert "2026" in result
    assert "https://news.example/asian-games" in result
    assert "Lin Shidong" in result
    assert "<b>" not in result
    urls = [url for url, _ in session.requests]
    assert "bing.com" in urls[0]
    assert any("format=rss" in u for u in urls)


def test_search_web_falls_back_to_duckduckgo_when_bing_empty():
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
    urls = [url for url, _ in session.requests]
    assert any("bing.com" in u for u in urls)
    ddg_urls = [u for u in urls if "api.duckduckgo.com/" in u]
    assert ddg_urls and "python%20language" in ddg_urls[0]


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

def test_normalize_query_splits_long_chinese_run():
    assert (
        online_tools._normalize_query("2026亚运会乒乓球男单冠军")
        == "2026 亚运会 乒乓球 男单 冠军"
    )
    assert (
        online_tools._normalize_query("请问2026年男子乒乓球亚运会冠军是谁")
        == "2026 男子 乒乓球 亚运会 冠军"
    )


def test_normalize_query_leaves_spaced_and_short_queries_alone():
    assert online_tools._normalize_query("亚运会 乒乓球 男单") == "亚运会 乒乓球 男单"
    assert online_tools._normalize_query("python language") == "python language"
    assert online_tools._normalize_query("乒乓球") == "乒乓球"


def test_search_web_requests_normalized_keywords():
    session = FakeSession(FakeResponse(body=BING_HTML))
    tool = online_tools.OnlineSearchTool()
    tool.session = session

    result = asyncio.run(tool.search_web("2026亚运会乒乓球男单冠军", max_results=3))

    assert "自动分词" in result
    first_url = session.requests[0][0]
    assert "%20" in first_url
    assert "2026%20%E4%BA%9A%E8%BF%90%E4%BC%9A" in first_url  # "2026 Ã¤ÂºÂÃ¨Â¿ÂÃ¤Â¼Â"\n

def test_search_web_drops_anchor_word_and_warns_when_irrelevant():
    """结果始终不相关时：应去掉首个关键词重试，并在返回里明确警告，防止模型误判"信息不存在"。"""
    session = FakeSession(FakeResponse(body=BING_HTML))
    tool = online_tools.OnlineSearchTool()
    tool.session = session

    result = asyncio.run(tool.search_web("量子计算 股票 预测 模型", max_results=3))

    assert "不相关" in result
    # 每个候选查询走 3 个 Bing 端点；出现第二轮请求说明发生了重试
    assert len(session.requests) >= 4
    retried = [u for u, _ in session.requests[3:]]
    assert any(online_tools.quote("股票 预测 模型") in u for u in retried)
