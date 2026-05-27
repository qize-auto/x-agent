"""
浏览器自动化工具
================
基于 Playwright 的浏览器控制，支持网页操作、截图、JS 执行。

可选依赖:
    pip install playwright
    playwright install chromium

降级方案:
    未安装时返回安装指引。
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Optional


class BrowserManager:
    """浏览器实例管理器（单例）"""
    _instance: "BrowserManager | None" = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._playwright = None
            cls._instance._browser = None
            cls._instance._page = None
        return cls._instance

    def _ensure_browser(self):
        """懒启动浏览器"""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError("Playwright 未安装。请运行: pip install playwright && playwright install chromium")

        if self._browser is None:
            self._playwright = sync_playwright().start()
            # Windows 下使用 chromium，无头模式
            self._browser = self._playwright.chromium.launch(headless=True)
            self._page = self._browser.new_page()

    def navigate(self, url: str) -> str:
        self._ensure_browser()
        self._page.goto(url, wait_until="networkidle", timeout=30000)
        title = self._page.title()
        return f"已导航至: {url}\n标题: {title}"

    def click(self, selector: str) -> str:
        self._ensure_browser()
        self._page.click(selector, timeout=10000)
        return f"已点击: {selector}"

    def type_text(self, selector: str, text: str, submit: bool = False) -> str:
        self._ensure_browser()
        self._page.fill(selector, text, timeout=10000)
        if submit:
            self._page.press(selector, "Enter")
        return f"已输入: {text} -> {selector}"

    def screenshot(self, path: str = "", full_page: bool = True) -> str:
        self._ensure_browser()
        if not path:
            path = str(Path.home() / ".xagent" / "screenshots" / "browser_screenshot.png")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._page.screenshot(path=path, full_page=full_page)
        return f"截图已保存: {path}"

    def evaluate(self, js: str) -> str:
        self._ensure_browser()
        result = self._page.evaluate(js)
        return json.dumps(result, ensure_ascii=False, default=str)

    def get_text(self, selector: str = "") -> str:
        self._ensure_browser()
        if selector:
            text = self._page.inner_text(selector, timeout=10000)
        else:
            text = self._page.inner_text("body")
        return text[:8000] + ("..." if len(text) > 8000 else "")

    def close(self):
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
        self._page = None


def register_browser_tools(registry):
    """注册浏览器自动化工具"""
    bm = BrowserManager()

    def browser_open(url: str) -> str:
        """打开浏览器并访问 URL"""
        try:
            return bm.navigate(url)
        except RuntimeError as e:
            return f"[错误] {e}"
        except Exception as e:
            return f"[错误] 浏览器操作失败: {e}"

    def browser_navigate(url: str) -> str:
        """在当前页面导航到新的 URL"""
        return browser_open(url)

    def browser_click(selector: str) -> str:
        """点击页面元素（CSS 选择器）"""
        try:
            return bm.click(selector)
        except Exception as e:
            return f"[错误] 点击失败: {e}"

    def browser_type(selector: str, text: str, submit: bool = False) -> str:
        """在输入框中输入文本"""
        try:
            return bm.type_text(selector, text, submit)
        except Exception as e:
            return f"[错误] 输入失败: {e}"

    def browser_screenshot(path: str = "", full_page: bool = True) -> str:
        """截取当前页面截图"""
        try:
            return bm.screenshot(path, full_page)
        except Exception as e:
            return f"[错误] 截图失败: {e}"

    def browser_evaluate(js: str) -> str:
        """在页面中执行 JavaScript 并返回结果"""
        try:
            return bm.evaluate(js)
        except Exception as e:
            return f"[错误] JS 执行失败: {e}"

    def browser_get_text(selector: str = "") -> str:
        """获取页面或元素的文本内容"""
        try:
            return bm.get_text(selector)
        except Exception as e:
            return f"[错误] 获取文本失败: {e}"

    def browser_close() -> str:
        """关闭浏览器"""
        bm.close()
        return "浏览器已关闭"

    registry.register(
        "browser_open", "打开浏览器并访问 URL",
        {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "目标 URL"},
            },
            "required": ["url"],
        },
        browser_open,
    )

    registry.register(
        "browser_click", "点击页面元素",
        {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS 选择器"},
            },
            "required": ["selector"],
        },
        browser_click,
    )

    registry.register(
        "browser_type", "在输入框中输入文本",
        {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS 选择器"},
                "text": {"type": "string", "description": "输入内容"},
                "submit": {"type": "boolean", "description": "是否按回车提交", "default": False},
            },
            "required": ["selector", "text"],
        },
        browser_type,
    )

    registry.register(
        "browser_screenshot", "截取页面截图",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "保存路径（为空使用默认）", "default": ""},
                "full_page": {"type": "boolean", "description": "是否截取整页", "default": True},
            },
            "required": [],
        },
        browser_screenshot,
    )

    registry.register(
        "browser_evaluate", "执行 JavaScript",
        {
            "type": "object",
            "properties": {
                "js": {"type": "string", "description": "JavaScript 代码"},
            },
            "required": ["js"],
        },
        browser_evaluate,
    )

    registry.register(
        "browser_get_text", "获取页面文本",
        {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS 选择器（为空获取整个页面）", "default": ""},
            },
            "required": [],
        },
        browser_get_text,
    )

    registry.register(
        "browser_close", "关闭浏览器",
        {
            "type": "object",
            "properties": {},
            "required": [],
        },
        browser_close,
    )
