#!/usr/bin/env python3
"""
联网工具模块，为计划模式提供网络搜索功能
"""

import asyncio
import base64
import html as html_lib
import re
from pathlib import Path
import aiohttp
from urllib.parse import quote, urlparse
import logging
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

# 浏览器 UA：部分搜索后端会拒绝明显的脚本 UA
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{5,}")

# 搜索引擎不需要的疑问词/助词/切分残留，避免污染查询
_QUERY_STOPWORDS = {
    "请问", "一下", "查询", "搜索", "告诉", "帮我", "我", "你", "他", "她", "它",
    "的", "了", "是", "在", "和", "与", "或", "于", "及", "等", "把", "被", "让",
    "什么", "怎么", "怎样", "如何", "多少", "谁", "哪", "哪个", "哪些", "吗", "呢",
    "啊", "呀", "吧", "年", "月", "日",
}


def _normalize_query(query: str) -> str:
    """长中文无空格查询会被搜索引擎切坏（只匹配开头几个字）。
    这里用 jieba 把它切成空格分隔的关键词；已带空格、纯英文或短查询原样返回。"""
    query = (query or "").strip()
    if not query or " " in query or not _CJK_RUN_RE.search(query):
        return query
    try:
        import jieba
    except ImportError:  # jieba 未安装时退回原行为
        return query
    words: list[str] = []
    for word in jieba.cut(query):
        word = word.strip()
        if not word or word in _QUERY_STOPWORDS or re.fullmatch(r"[\W_]+", word):
            continue
        if word not in words:
            words.append(word)
    return " ".join(words[:6]) if words else query


def _results_are_relevant(keywords: str, items: list[dict]) -> bool:
    """检查前几条结果的标题/摘要里是否至少出现一个中文关键词。
    Bing 对含纯数字年份的查询容易整页跑偏（全是"2026年"百科/放假页），用这个做兜底判断。"""
    cjk_words = [w for w in keywords.split() if len(w) >= 2 and re.search(r"[\u4e00-\u9fff]", w)]
    if not cjk_words:
        return True
    # 用"不同关键词的覆盖数"而不是"任一关键词命中"：
    # 结果页常锚定在某个泛词上（如"男子"→词典页），那个词本身就是查询词，会骗过宽松检查。
    blob = " ".join(f"{i.get('title', '')} {i.get('content', '')}" for i in items[:3])
    matched = {w for w in cjk_words if w in blob}
    return len(matched) >= min(2, len(cjk_words))


def _strip_tags(fragment: str) -> str:
    fragment = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", fragment, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def _clean_bing_url(href: str) -> str:
    """Bing 结果链接常包一层 /ck/a?...&u=a1<base64url> 跳转，解出真实地址。"""
    href = html_lib.unescape(href)
    if "/ck/a" in href:
        m = re.search(r"[?&]u=([^&]+)", href)
        if m:
            token = m.group(1)
            if token.startswith("a1"):
                token = token[2:]
            try:
                padded = token + "=" * (-len(token) % 4)
                real = base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
                if real.startswith(("http://", "https://")):
                    return real
            except Exception:
                pass
    return href


def _parse_bing_html(html_text: str) -> list[dict]:
    """解析 Bing 普通网页搜索结果（SERP），质量远好于 RSS；无结果返回空列表。"""
    items = []
    for block in re.findall(r'<li class="b_algo[^"]*"[^>]*>(.*?)</li>', html_text, flags=re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.S)
        if not m:
            continue
        url = _clean_bing_url(m.group(1))
        title = _strip_tags(m.group(2))
        pm = re.search(r"<p[^>]*>(.*?)</p>", block, flags=re.S)
        content = _strip_tags(pm.group(1)) if pm else ""
        if title and "bing.com" not in url:
            items.append({"title": title, "content": content, "url": url})
    return items


def _parse_rss_items(xml_text: str) -> list[dict]:
    """解析 Bing RSS 搜索结果，返回 [{title, content, url}]；解析失败返回空列表。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = re.sub(r"<[^>]+>", "", item.findtext("description") or "").strip()
        if title or link:
            items.append({"title": title or link, "content": description, "url": link})
    return items


def _format_results(query: str, results: list[dict], max_results: int) -> str:
    output = f"网络搜索结果：{query}\n\n"
    for i, result in enumerate(results[:max_results], 1):
        output += f"{i}. {result['title']}\n"
        if result.get("content"):
            content = result["content"]
            if len(content) > 300:
                content = content[:300] + "..."
            output += f"   内容: {content}\n"
        if result.get("url"):
            output += f"   \u94fe\u63a5: {result['url']}\n"
        output += "\n"
    return output


def _network_error(operation: str, error: Exception) -> str:
    detail = str(error).strip() or error.__class__.__name__
    if isinstance(error, asyncio.TimeoutError):
        detail = "请求超时"
    return (
        f"网络{operation}失败（{error.__class__.__name__}: {detail}）。"
        "请检查网络连接、防火墙或代理设置；不要根据此错误推断搜索结果。"
    )

class OnlineSearchTool:
    """网络搜索工具，用于获取最新信息"""

    def __init__(self):
        self.session = None
        self.timeout = 10

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 aiohttp 会话"""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self.session = aiohttp.ClientSession(timeout=timeout, trust_env=True)
        return self.session

    async def search_web(self, query: str, max_results: int = 5) -> str:
        """
        搜索网络信息。

        流程：jieba 自动分词 -> 依次尝试后端（Bing SERP -> Bing RSS -> DuckDuckGo）
        -> 检查结果相关性，若被数字年份带偏则去掉数字重试 -> 返回格式化结果。

        Args:
            query: 搜索查询字符串
            max_results: 最大返回结果数

        Returns:
            搜索结果的格式化字符串
        """
        query = (query or "").strip()
        keywords = _normalize_query(query)
        candidates = [keywords]
        digitless = " ".join(w for w in keywords.split() if not re.fullmatch(r"\d{3,4}", w))
        if digitless and digitless != keywords:
            candidates.append(digitless)
        # Bing 会过度锚定第一个关键词（如"男子 乒乓球 亚运会"整页返回"男子"词典页），
        # 结果不相关时再去掉首词重试一轮。
        base = candidates[-1].split()
        if len(base) >= 3:
            dropped = " ".join(base[1:])
            if dropped and dropped not in candidates:
                candidates.append(dropped)
        # 终极候选：只留中文关键词并按长度降序排列（长词更具体，如"亚运会"优先于"男子"）
        cjk_only = [w for w in keywords.split() if re.search(r"[\u4e00-\u9fff]", w)]
        if len(cjk_only) >= 2:
            ordered = " ".join(sorted(cjk_only, key=len, reverse=True))
            if ordered not in candidates:
                candidates.append(ordered)

        errors: list[str] = []
        skipped: set[str] = set()  # 本次调用里网络级失败的后端，换词重试时不再浪费时间
        first_hit: tuple[str, list[dict]] | None = None
        for kw in candidates:
            for backend_name, backend in (
                ("Bing", self._search_bing),
                ("DuckDuckGo", self._search_duckduckgo),
            ):
                if backend_name in skipped:
                    continue
                try:
                    items = await backend(kw, max_results)
                except Exception as e:
                    detail = str(e).strip() or e.__class__.__name__
                    if isinstance(e, asyncio.TimeoutError):
                        detail = f"请求超时（{self.timeout}s）"
                    logger.error("网络搜索后端 %s 失败: %s", backend_name, e)
                    errors.append(f"{backend_name} {e.__class__.__name__}: {detail}")
                    skipped.add(backend_name)
                    continue
                if not items:
                    continue
                if first_hit is None:
                    first_hit = (kw, items)
                if _results_are_relevant(kw, items):
                    return self._finish_search(query, kw, items, max_results)

        if first_hit:
            return self._finish_search(query, first_hit[0], first_hit[1], max_results, relevant=False)
        if errors:
            return (
                f"网络搜索失败（{'；'.join(errors)}）。"
                "请检查网络连接、防火墙或代理设置；不要根据此错误推断搜索结果。"
            )
        return f"未找到关于 '{query}' 的相关信息。"

    @staticmethod
    def _finish_search(query: str, kw: str, items: list[dict], max_results: int,
                       relevant: bool = True) -> str:
        output = _format_results(kw, items, max_results)
        if kw != query:
            note = "原查询已自动分词为关键词" if kw == _normalize_query(query) else "已调整搜索关键词"
            output = f"（{note}：{kw}）\n" + output
        if not relevant:
            output = "警告：搜索结果可能与问题不相关，不要据此断言信息不存在；请换用更具体的关键词（人名/队名/场馆等）重试。\n" + output
        return output

    async def _search_bing(self, query: str, max_results: int) -> list[dict]:
        """Bing 搜索：优先解析普通 SERP 网页（相关性好），失败再退回 RSS 接口。"""
        last_error: Exception | None = None
        session = await self._get_session()
        headers = {
            "User-Agent": _BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        fetch_count = max(10, max_results * 2)
        attempts = [
            (f"https://www.bing.com/search?q={quote(query)}&count={fetch_count}", _parse_bing_html),
            (f"https://cn.bing.com/search?q={quote(query)}&count={fetch_count}", _parse_bing_html),
            (f"https://www.bing.com/search?q={quote(query)}&format=rss&count={max(1, max_results)}", _parse_rss_items),
        ]
        for url, parser in attempts:
            try:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        last_error = RuntimeError(f"HTTP {response.status}")
                        continue
                    body = await response.text()
            except Exception as e:
                last_error = e
                continue
            items = parser(body)
            if items:
                return items
        if last_error:
            raise last_error
        return []

    async def _search_duckduckgo(self, query: str, max_results: int) -> list[dict]:
        """DuckDuckGo Instant Answer API（无需 API key），作为国际网络的兜底后端。"""
        session = await self._get_session()
        search_url = f"https://api.duckduckgo.com/?q={quote(query)}&format=json&no_html=1&skip_disambig=1"

        async with session.get(
            search_url,
            headers={"User-Agent": "Lily/1.0 web search"},
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
            data = await response.json()

            results: list[dict] = []

            # 添加即时答案（如果有）
            if data.get("AbstractText"):
                results.append({
                    "title": data.get("Heading", "即时答案"),
                    "content": data["AbstractText"],
                    "url": data.get("AbstractURL", ""),
                })

            # 添加相关话题
            if data.get("RelatedTopics"):
                for topic in data["RelatedTopics"][:max_results]:
                    if "Text" in topic:
                        results.append({
                            "title": topic.get("FirstURL", "").split("/")[-1].replace("_", " "),
                            "content": topic["Text"],
                            "url": topic.get("FirstURL", ""),
                        })

            return results

    async def get_web_content(self, url: str, max_length: int = 2000) -> str:
        """
        获取网页内容

        Args:
            url: 网页URL
            max_length: 最大内容长度

        Returns:
            网页内容的摘要
        """
        try:
            # 验证URL
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                return "无效的URL格式"

            session = await self._get_session()

            # 设置User-Agent
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }

            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    content = await response.text()

                    # 提取标题
                    title_match = re.search(r'<title[^>]*>(.*?)</title>', content, re.IGNORECASE)
                    title = title_match.group(1) if title_match else "无标题"

                    # 提取主要内容（简单实现）
                    # 移除脚本和样式
                    clean_content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL)
                    clean_content = re.sub(r'<style[^>]*>.*?</style>', '', clean_content, flags=re.DOTALL)

                    # 提取文本内容
                    text_content = re.sub(r'<[^>]+>', ' ', clean_content)
                    text_content = re.sub(r'\s+', ' ', text_content).strip()

                    # 截取主要内容
                    if len(text_content) > max_length:
                        text_content = text_content[:max_length] + "... [内容截断]"

                    return f"网页标题: {title}\n\n内容摘要:\n{text_content}"

                return f"网页访问失败（HTTP {response.status}）。不要根据此错误推断页面内容。"

        except Exception as e:
            logger.error(f"获取网页内容错误: {e}")
            return _network_error("取网页内容", e)

    async def http_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json_body: dict | None = None,
        body: str | None = None,
        timeout: float = 60,
        save_to: str | None = None,
        max_length: int = 4000,
    ) -> str:
        """发送任意 HTTP 请求，用于调用外部 API。

        save_to 用于二进制响应（如图片）：把原始字节写进文件并返回路径与大小。
        """
        method = (method or "GET").upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
            return f"Error: 不支持的请求方法 {method}"

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "Error: url 必须是以 http:// 或 https:// 开头的完整地址"

        send_headers = {str(k): str(v) for k, v in (headers or {}).items()}
        kwargs: dict = {"headers": send_headers}
        if json_body is not None and body:
            return "Error: json_body 和 body 只能二选一"
        if json_body is not None:
            kwargs["json"] = json_body
        elif body:
            kwargs["data"] = body.encode("utf-8")
        if not send_headers:
            send_headers["User-Agent"] = "Lily/1.0"

        try:
            session = await self._get_session()
            request_timeout = aiohttp.ClientTimeout(total=max(1.0, float(timeout)))
            async with session.request(method, url, timeout=request_timeout, **kwargs) as response:
                content_type = response.headers.get("Content-Type", "")
                raw = await response.read()
                status_line = f"HTTP {response.status} ({content_type or 'unknown content-type'})"

                if save_to:
                    target = Path(save_to)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(raw)
                    return f"{status_line}\nSaved {len(raw)} bytes to {target}"

                text = raw.decode("utf-8", errors="replace")
                if len(text) > max_length:
                    text = text[:max_length] + "... [内容截断]"
                return f"{status_line}\n{text}"
        except asyncio.TimeoutError:
            return f"Error: 请求超时（{timeout}s）"
        except Exception as e:
            logger.error(f"HTTP 请求错误: {e}")
            return _network_error("HTTP 请求", e)

    async def close(self):
        """关闭会话"""
        if self.session and not self.session.closed:
            await self.session.close()

# 全局实例
online_search_tool = OnlineSearchTool()

async def search_web(query: str, max_results: int = 5) -> str:
    """网络搜索函数接口"""
    return await online_search_tool.search_web(query, max_results)

async def get_web_content(url: str, max_length: int = 2000) -> str:
    """获取网页内容函数接口"""
    return await online_search_tool.get_web_content(url, max_length)

async def http_request(**kwargs) -> str:
    """发送任意 HTTP 请求函数接口"""
    return await online_search_tool.http_request(**kwargs)

async def close_online_tools():
    """关闭联网工具"""
    await online_search_tool.close()
