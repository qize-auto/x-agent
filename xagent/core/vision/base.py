"""视觉感知基础层

统一的数据模型和抽象接口，所有感知器输出格式一致。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class UIElement:
    """UI 元素"""
    element_id: str
    element_type: str          # button, input, link, text, image, etc.
    label: str = ""            # 可读标签
    bbox: tuple[int, int, int, int] = field(default_factory=lambda: (0, 0, 0, 0))  # x, y, w, h
    state: str = ""            # focused, checked, disabled, etc.
    text: str = ""             # 内部文本
    clickable: bool = False
    editable: bool = False

    def to_markdown(self) -> str:
        parts = [f"- [{self.element_type}]"]
        if self.label:
            parts.append(f"'{self.label}'")
        if self.text and self.text != self.label:
            parts.append(f"text='{self.text[:50]}'")
        if self.state:
            parts.append(f"({self.state})")
        if self.clickable:
            parts.append("[clickable]")
        if self.editable:
            parts.append("[editable]")
        return " ".join(parts)


@dataclass
class UIPerception:
    """统一的 UI 感知结果"""
    source: str                  # a11y | ocr | multimodal
    ui_type: str                 # desktop_window | browser_page | dialog | unknown
    title: str = ""
    url: str = ""               # 浏览器场景
    elements: list[UIElement] = field(default_factory=list)
    raw_text: str = ""          # 原始提取文本
    screenshot_path: str = ""   # 截图保存路径（如有）

    def to_context_string(self, max_elements: int = 30) -> str:
        """转为 LLM 可读的上下文字符串"""
        lines = ["--- UI State ---"]
        if self.title:
            lines.append(f"Title: {self.title}")
        if self.url:
            lines.append(f"URL: {self.url}")
        lines.append(f"Source: {self.source}")
        lines.append("")
        lines.append("Elements:")

        elems = self.elements[:max_elements]
        for e in elems:
            lines.append(e.to_markdown())

        if len(self.elements) > max_elements:
            lines.append(f"... ({len(self.elements) - max_elements} more elements)")

        if self.raw_text and len(self.raw_text) > 100:
            lines.append("")
            lines.append("Visible text:")
            lines.append(self.raw_text[:500])

        lines.append("---")
        return "\n".join(lines)

    def find(self, element_type: str = None, label_contains: str = None) -> list[UIElement]:
        """按条件查找元素"""
        results = []
        for e in self.elements:
            if element_type and e.element_type != element_type:
                continue
            if label_contains and label_contains.lower() not in (e.label + e.text).lower():
                continue
            results.append(e)
        return results


class BasePerceptor(ABC):
    """感知器抽象基类"""

    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool:
        """当前环境下是否可用"""
        ...

    @abstractmethod
    def perceive(self, target: str = "screen", **kwargs) -> UIPerception:
        """
        执行感知。

        Args:
            target: "screen" | "window" | "browser" | 文件路径
            **kwargs: 策略特定参数
        """
        ...
