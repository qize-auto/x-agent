"""Multimodal Perceptor

使用多模态 LLM（GPT-4o / Gemini / Claude）直接理解截图。
功能最强，但成本最高。
"""
from __future__ import annotations
import base64
import io
from pathlib import Path
from typing import Optional

from .base import BasePerceptor, UIElement, UIPerception

# 已知支持视觉的模型标识（小写匹配）
VISION_MODEL_PATTERNS = [
    "gpt-4o", "gpt-4-turbo",
    "gemini-1.5", "gemini-1.5-pro", "gemini-1.5-flash",
    "claude-3-5-sonnet", "claude-3-opus", "claude-3-sonnet",
    "qwen-vl", "llava", "cogvlm", "bakllava",
]


class MultimodalPerceptor(BasePerceptor):
    """
    多模态 LLM 视觉感知器。

    用法:
        perceptor = MultimodalPerceptor(llm_client)
        if perceptor.is_available():
            perception = perceptor.perceive("screen")
    """

    name = "multimodal"

    def __init__(self, llm_client=None):
        self.llm = llm_client

    @classmethod
    def is_vision_model(cls, model_id: str) -> bool:
        """判断 model_id 是否为视觉模型"""
        if not model_id:
            return False
        lower = model_id.lower()
        return any(pat in lower for pat in VISION_MODEL_PATTERNS)

    def is_available(self) -> bool:
        if self.llm is None:
            return False
        model_id = getattr(self.llm, "model_id", "")
        provider = getattr(self.llm, "provider", "")
        return self.is_vision_model(model_id) or self.is_vision_model(provider)

    def perceive(self, target: str = "screen", **kwargs) -> UIPerception:
        """
        Args:
            target: "screen" | 图片路径
            **kwargs: screenshot_callback — 自定义截图函数
        """
        if not self.is_available():
            return UIPerception(source="multimodal", ui_type="unknown",
                                title="No vision model available")

        # 1. 获取图片
        img_bytes = self._get_image_bytes(target, kwargs.get("screenshot_callback"))
        if img_bytes is None:
            return UIPerception(source="multimodal", ui_type="unknown",
                                title="Image capture failed")

        # 2. 编码为 base64
        b64_image = base64.b64encode(img_bytes).decode("utf-8")

        # 3. 调用多模态 LLM
        try:
            description = self._describe_with_llm(b64_image)
        except Exception as e:
            return UIPerception(source="multimodal", ui_type="unknown",
                                title=f"LLM error: {e}")

        # 4. 解析描述为结构化元素（简化：将整个描述作为 raw_text）
        return UIPerception(
            source="multimodal",
            ui_type="unknown",  # LLM 可能从图中判断
            title=description[:50],
            raw_text=description,
            elements=self._parse_elements_from_description(description),
        )

    def _describe_with_llm(self, b64_image: str) -> str:
        """调用多模态 LLM 描述图片"""
        provider = getattr(self.llm, "provider", "").lower()
        model_id = getattr(self.llm, "model_id", "")

        prompt = (
            "Analyze this screenshot. Describe the UI layout, list all interactive elements "
            "(buttons, inputs, links), and note any important text. Be concise."
        )

        if "gemini" in provider or "gemini" in model_id:
            return self._call_gemini(b64_image, prompt)
        elif "claude" in provider or "claude" in model_id:
            return self._call_claude(b64_image, prompt)
        else:
            # 默认 OpenAI 格式（GPT-4o, Qwen-VL, 等）
            return self._call_openai_format(b64_image, prompt)

    def _call_openai_format(self, b64_image: str, prompt: str) -> str:
        """OpenAI 兼容格式"""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}},
                ],
            }
        ]
        resp = self.llm.chat(messages)
        return resp.content

    def _call_gemini(self, b64_image: str, prompt: str) -> str:
        """Gemini 格式（如果有特殊处理需求）"""
        # Gemini 也支持 OpenAI 兼容格式，直接复用
        return self._call_openai_format(b64_image, prompt)

    def _call_claude(self, b64_image: str, prompt: str) -> str:
        """Claude 格式（Anthropic 原生 API）"""
        # Claude 3 支持 base64 image，也通过 OpenAI 兼容端点可用
        # 如果用户配置了 Anthropic 原生端点，这里需要适配
        # 简化：尝试 OpenAI 兼容格式
        try:
            return self._call_openai_format(b64_image, prompt)
        except Exception:
            return "[Claude vision not available via current endpoint]"

    @staticmethod
    def _get_image_bytes(target: str, screenshot_callback=None) -> Optional[bytes]:
        """获取图片的字节数据"""
        from PIL import Image

        if target == "screen":
            if screenshot_callback:
                img = screenshot_callback()
            else:
                try:
                    import mss
                    with mss.mss() as sct:
                        monitor = sct.monitors[1]
                        screenshot = sct.grab(monitor)
                        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
                except Exception:
                    return None
        else:
            path = Path(target)
            if path.exists():
                img = Image.open(path)
            else:
                return None

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _parse_elements_from_description(description: str) -> list[UIElement]:
        """从 LLM 文本描述中启发式提取元素"""
        elements = []
        lines = description.splitlines()
        for i, line in enumerate(lines):
            line_lower = line.lower()
            if any(k in line_lower for k in ["button", "btn", "click"]):
                label = line.strip("- *•").split(":")[-1].strip()
                elements.append(UIElement(element_id=f"mm_{i}", element_type="button", label=label, clickable=True))
            elif any(k in line_lower for k in ["input", "textbox", "field", "text box"]):
                label = line.strip("- *•").split(":")[-1].strip()
                elements.append(UIElement(element_id=f"mm_{i}", element_type="input", label=label, editable=True))
            elif any(k in line_lower for k in ["link", "href"]):
                label = line.strip("- *•").split(":")[-1].strip()
                elements.append(UIElement(element_id=f"mm_{i}", element_type="link", label=label, clickable=True))
        return elements
