"""Vision — 视觉感知层

支持三种策略：
- a11y: Playwright Accessibility Tree（浏览器纯文本，零成本）
- hybrid: OCR + 元素检测（本地运行，低依赖）
- multimodal: 多模态 LLM（GPT-4o / Gemini / Claude，功能最强）

自动检测策略优先级：
1. 如果当前 LLM 是视觉模型 → multimodal
2. 如果是浏览器环境 → a11y
3. 否则 → hybrid
"""
from .perceptor import VisionPerceptor
from .base import UIElement, UIPerception

__all__ = ["VisionPerceptor", "UIElement", "UIPerception"]
