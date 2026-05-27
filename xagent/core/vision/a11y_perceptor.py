"""Accessibility Tree Perceptor

基于 Playwright 的 Accessibility Snapshot，纯文本、零 API 成本。
最适合浏览器自动化场景。
"""
from __future__ import annotations
from typing import Optional

from .base import BasePerceptor, UIElement, UIPerception


class A11yPerceptor(BasePerceptor):
    """
    Playwright Accessibility Tree 感知器。

    用法:
        perceptor = A11yPerceptor()
        if perceptor.is_available():
            perception = perceptor.perceive(page=playwright_page)
    """

    name = "a11y"

    def is_available(self) -> bool:
        try:
            from playwright.sync_api import Page
            return True
        except Exception:
            return False

    def perceive(self, target: str = "screen", **kwargs) -> UIPerception:
        """
        Args:
            target: 忽略，总是感知传入的 page
            page: Playwright Page 对象（必需）
        """
        page = kwargs.get("page")
        if page is None:
            return UIPerception(source="a11y", ui_type="unknown", title="No page provided")

        try:
            snapshot = page.accessibility.snapshot()
            return self._parse_snapshot(snapshot, page.url)
        except Exception as e:
            return UIPerception(source="a11y", ui_type="unknown", title=f"Error: {e}")

    def _parse_snapshot(self, snapshot: dict, url: str) -> UIPerception:
        """解析 Playwright accessibility snapshot"""
        elements = []
        title = snapshot.get("name", "")
        role = snapshot.get("role", "")

        def walk(node: dict, parent_path: str = ""):
            name = node.get("name", "")
            node_role = node.get("role", "")
            value = node.get("value", "")
            children = node.get("children", [])

            # 映射 role 到 element_type
            role_map = {
                "button": "button",
                "link": "link",
                "textbox": "input",
                "checkbox": "checkbox",
                "radio": "radio",
                "heading": "heading",
                "img": "image",
                "list": "list",
                "listitem": "listitem",
                "paragraph": "text",
                "combobox": "select",
            }
            elem_type = role_map.get(node_role, node_role)

            # 只保留有意义的元素
            if name or value or node_role in role_map:
                elements.append(UIElement(
                    element_id=f"{parent_path}/{node_role}_{len(elements)}",
                    element_type=elem_type,
                    label=name,
                    text=value,
                    clickable=node_role in ("button", "link"),
                    editable=node_role in ("textbox", "combobox"),
                ))

            for i, child in enumerate(children):
                walk(child, f"{parent_path}/{i}")

        if role:
            walk(snapshot)

        return UIPerception(
            source="a11y",
            ui_type="browser_page",
            title=title,
            url=url,
            elements=elements,
            raw_text=self._extract_text(snapshot),
        )

    @staticmethod
    def _extract_text(node: dict) -> str:
        """从 snapshot 提取纯文本"""
        texts = []

        def walk(n):
            name = n.get("name", "")
            if name and n.get("role") not in ("generic", "none"):
                texts.append(name)
            for child in n.get("children", []):
                walk(child)

        walk(node)
        return "\n".join(texts[:50])  # 限制行数
