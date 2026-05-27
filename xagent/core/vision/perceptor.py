"""VisionPerceptor — 视觉感知主入口

自动检测最佳策略，统一输出格式。
"""
from __future__ import annotations
from typing import Optional

from .base import BasePerceptor, UIPerception
from .a11y_perceptor import A11yPerceptor
from .ocr_perceptor import OCRPerceptor
from .multimodal_perceptor import MultimodalPerceptor


class VisionPerceptor(BasePerceptor):
    """
    视觉感知主类，自动选择最佳策略。

    策略优先级（auto 模式）：
    1. multimodal — 如果配置的 LLM 是视觉模型（GPT-4o / Gemini / Claude 3.5）
    2. a11y — 如果提供了 Playwright page 对象（浏览器环境）
    3. hybrid — 截图 + OCR（保底方案）

    用法:
        perceptor = VisionPerceptor(llm_client=llm)
        perception = perceptor.perceive("screen")
        print(perception.to_context_string())

        # 浏览器场景
        perception = perceptor.perceive("browser", page=playwright_page)
    """

    name = "auto"

    def __init__(self, llm_client=None, strategy: str = "auto", config: dict = None):
        self.llm_client = llm_client
        self.strategy = strategy
        self.config = config or {}
        self._perceptors: dict[str, BasePerceptor] = {}
        self._selected: Optional[str] = None

    def is_available(self) -> bool:
        """至少有一种感知策略可用"""
        return any(p.is_available() for p in self._get_all_perceptors().values())

    def perceive(self, target: str = "screen", **kwargs) -> UIPerception:
        """
        执行视觉感知。

        Args:
            target: "screen" | "browser" | 图片路径
            **kwargs:
                - page: Playwright Page（a11y 模式需要）
                - screenshot_callback: 自定义截图函数
                - force_strategy: 强制使用指定策略
        """
        force = kwargs.get("force_strategy")
        if force:
            perceptor = self._get_perceptor(force)
            if perceptor and perceptor.is_available():
                return perceptor.perceive(target, **kwargs)
            return UIPerception(source="auto", ui_type="unknown",
                                title=f"Strategy '{force}' not available")

        # auto 策略选择
        selected = self._select_strategy(target, kwargs)
        perceptor = self._get_perceptor(selected)
        if perceptor is None:
            return UIPerception(source="auto", ui_type="unknown",
                                title="No vision strategy available")

        return perceptor.perceive(target, **kwargs)

    def get_strategy(self) -> str:
        """返回当前实际使用的策略"""
        if self._selected:
            return self._selected
        # 做一遍选择但不执行
        dummy = self._select_strategy("screen", {})
        return dummy

    # ── 内部 ──

    def _get_all_perceptors(self) -> dict[str, BasePerceptor]:
        """懒加载所有感知器"""
        if not self._perceptors:
            self._perceptors["multimodal"] = MultimodalPerceptor(self.llm_client)
            self._perceptors["a11y"] = A11yPerceptor()
            self._perceptors["hybrid"] = OCRPerceptor()
        return self._perceptors

    def _get_perceptor(self, name: str) -> Optional[BasePerceptor]:
        return self._get_all_perceptors().get(name)

    def _select_strategy(self, target: str, kwargs: dict) -> str:
        """自动选择最佳策略"""
        if self.strategy != "auto":
            return self.strategy

        # 1. 如果是浏览器环境且有 page → a11y（零成本、结构化）
        if target == "browser" or kwargs.get("page") is not None:
            a11y = self._get_perceptor("a11y")
            if a11y and a11y.is_available():
                self._selected = "a11y"
                return "a11y"

        # 2. 如果 LLM 是视觉模型 → multimodal（功能最强）
        mm = self._get_perceptor("multimodal")
        if mm and mm.is_available():
            self._selected = "multimodal"
            return "multimodal"

        # 3. 兜底 → hybrid（OCR，本地运行）
        hybrid = self._get_perceptor("hybrid")
        if hybrid and hybrid.is_available():
            self._selected = "hybrid"
            return "hybrid"

        # 4. 都不成，返回第一个可用的
        for name, p in self._get_all_perceptors().items():
            if p.is_available():
                self._selected = name
                return name

        return "none"
