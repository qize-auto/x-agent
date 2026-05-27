"""
Web 搜索工具
===========
无需 API key 的方案优先（DuckDuckGo），支持 SearXNG 自托管。
"""
from __future__ import annotations
import urllib.request
import urllib.parse
import json
import re
from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    """简单 HTML 文本提取器"""
    def __init__(self):
        super().__init__()
        self.texts = []
        self.skip_tags = {"script", "style", "nav", "footer", "header"}
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.skip_tags:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self.skip_tags:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0:
            self.texts.append(data.strip())

    def get_text(self):
        return " ".join(t for t in self.texts if t)


def register_web_tools(registry):
    """注册 Web 相关工具"""

    def web_search(query: str, num_results: int = 5) -> str:
        """使用 DuckDuckGo 搜索（无需 API key）"""
        try:
            # DuckDuckGo HTML 搜索
            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0"
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            # 解析结果
            results = []
            # 简单正则提取
            for m in re.finditer(r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html):
                link = m.group(1)
                title = re.sub(r'<[^>]+>', '', m.group(2))
                if link.startswith("//"):
                    link = "https:" + link
                results.append(f"{title}\n  {link}")
                if len(results) >= num_results:
                    break

            return "\n\n".join(results) if results else "未找到搜索结果"
        except Exception as e:
            return f"[错误] 搜索失败: {e}"

    def web_fetch(url: str, max_length: int = 8000) -> str:
        """抓取网页内容并提取文本"""
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0"
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            extractor = _TextExtractor()
            try:
                extractor.feed(html)
            except Exception:
                pass
            text = extractor.get_text()
            return text[:max_length] + ("..." if len(text) > max_length else "")
        except Exception as e:
            return f"[错误] 抓取失败: {e}"

    registry.register(
        "web_search", "搜索网页（无需 API key）",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "num_results": {"type": "integer", "description": "结果数量", "default": 5},
            },
            "required": ["query"],
        },
        web_search,
    )

    registry.register(
        "web_fetch", "抓取网页内容并提取纯文本",
        {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "网页 URL"},
                "max_length": {"type": "integer", "description": "最大字符数", "default": 8000},
            },
            "required": ["url"],
        },
        web_fetch,
    )
