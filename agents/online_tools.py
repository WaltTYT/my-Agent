#!/usr/bin/env python3
"""
联网工具模块，为计划模式提供网络搜索功能
"""

import asyncio
import json
import re
from typing import Dict, Any, Optional
import aiohttp
from urllib.parse import quote, urlparse
import logging

logger = logging.getLogger(__name__)


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
        搜索网络信息

        Args:
            query: 搜索查询字符串
            max_results: 最大返回结果数

        Returns:
            搜索结果的格式化字符串
        """
        try:
            session = await self._get_session()

            # 使用 DuckDuckGo Instant Answer API (无需API key)
            search_url = f"https://api.duckduckgo.com/?q={quote(query)}&format=json&no_html=1&skip_disambig=1"

            async with session.get(
                search_url,
                headers={"User-Agent": "Lily/1.0 web search"},
            ) as response:
                if response.status == 200:
                    data = await response.json()

                    # 处理搜索结果
                    results = []

                    # 添加即时答案（如果有）
                    if data.get('AbstractText'):
                        results.append({
                            'title': data.get('Heading', '即时答案'),
                            'content': data['AbstractText'],
                            'url': data.get('AbstractURL', ''),
                            'type': 'answer'
                        })

                    # 添加相关话题
                    if data.get('RelatedTopics'):
                        for topic in data['RelatedTopics'][:max_results]:
                            if 'Text' in topic:
                                results.append({
                                    'title': topic.get('FirstURL', '').split('/')[-1].replace('_', ' '),
                                    'content': topic['Text'],
                                    'url': topic.get('FirstURL', ''),
                                    'type': 'topic'
                                })

                    # 格式化输出
                    if not results:
                        return f"未找到关于 '{query}' 的相关信息。"

                    output = f"网络搜索结果：{query}\n\n"
                    for i, result in enumerate(results[:max_results], 1):
                        output += f"{i}. {result['title']}\n"
                        if result['content']:
                            # 截断长内容
                            content = result['content'][:300] + "..." if len(result['content']) > 300 else result['content']
                            output += f"   内容: {content}\n"
                        if result['url']:
                            output += f"   链接: {result['url']}\n"
                        output += "\n"

                    return output

                return f"网络搜索失败（HTTP {response.status}）。不要根据此错误推断搜索结果。"

        except Exception as e:
            logger.error(f"网络搜索错误: {e}")
            return _network_error("搜索", e)

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

async def close_online_tools():
    """关闭联网工具"""
    await online_search_tool.close()
