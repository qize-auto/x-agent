"""OCR Hybrid Perceptor

截图 + OCR 文本提取 + 轻量元素检测。
无需多模态 LLM，纯本地运行。

OCR 引擎优先级：
1. easyocr（效果最好，PyTorch 依赖）
2. pytesseract（轻量，需系统安装 tesseract）
3. 占位模式（仅返回截图，无 OCR）
"""
from __future__ import annotations
import base64
import io
import tempfile
from pathlib import Path
from typing import Optional

from .base import BasePerceptor, UIElement, UIPerception


class OCRPerceptor(BasePerceptor):
    """
    OCR 混合感知器。

    用法:
        perceptor = OCRPerceptor()
        if perceptor.is_available():
            perception = perceptor.perceive("screen")
            # perception.raw_text 包含 OCR 提取的文本
            # perception.elements 包含检测到的文本块
    """

    name = "ocr"

    def __init__(self):
        self._ocr_engine = None
        self._ocr_name = "none"

    def is_available(self) -> bool:
        """只要截图功能可用就算可用"""
        try:
            from PIL import Image
            return True
        except Exception:
            return False

    def _init_ocr(self):
        """懒加载 OCR 引擎"""
        if self._ocr_engine is not None:
            return

        # 1. 尝试 easyocr
        try:
            import easyocr
            self._ocr_engine = easyocr.Reader(["ch_sim", "en"], gpu=False)
            self._ocr_name = "easyocr"
            return
        except Exception:
            pass

        # 2. 尝试 pytesseract
        try:
            import pytesseract
            self._ocr_engine = pytesseract
            self._ocr_name = "pytesseract"
            return
        except Exception:
            pass

    def perceive(self, target: str = "screen", **kwargs) -> UIPerception:
        """
        Args:
            target: "screen" | 图片文件路径
            **kwargs: screenshot_callback — 自定义截图函数
        """
        # 1. 获取截图
        img = self._capture(target, kwargs.get("screenshot_callback"))
        if img is None:
            return UIPerception(source="ocr", ui_type="unknown", title="Screenshot failed")

        # 2. 保存截图到临时文件（供后续使用）
        tmp_path = self._save_temp(img)

        # 3. OCR 提取
        self._init_ocr()
        text_blocks = self._extract_text(img)

        # 4. 构建元素列表
        elements = []
        for i, block in enumerate(text_blocks):
            elements.append(UIElement(
                element_id=f"ocr_{i}",
                element_type=block.get("type", "text"),
                label=block.get("text", "")[:50],
                text=block.get("text", ""),
                bbox=block.get("bbox", (0, 0, 0, 0)),
                clickable=block.get("type") == "button",
            ))

        raw_text = "\n".join(b.get("text", "") for b in text_blocks)

        return UIPerception(
            source=f"ocr:{self._ocr_name}",
            ui_type="desktop_window" if target == "screen" else "unknown",
            title=raw_text[:50],
            elements=elements,
            raw_text=raw_text[:1000],
            screenshot_path=str(tmp_path) if tmp_path else "",
        )

    # ── 内部实现 ──

    def _capture(self, target: str, screenshot_callback=None):
        """截图或加载图片"""
        from PIL import Image

        if target == "screen":
            if screenshot_callback:
                return screenshot_callback()
            try:
                # 使用 mss 跨平台截图
                import mss
                with mss.mss() as sct:
                    monitor = sct.monitors[1]  # 主显示器
                    screenshot = sct.grab(monitor)
                    return Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
            except Exception:
                # fallback: 返回空白图
                return Image.new("RGB", (800, 600), color="white")
        else:
            # 加载已有图片
            path = Path(target)
            if path.exists():
                return Image.open(path)
            return None

    @staticmethod
    def _save_temp(img) -> Optional[Path]:
        try:
            tmp = Path(tempfile.gettempdir()) / f"xagent_screenshot_{id(img)}.png"
            img.save(tmp)
            return tmp
        except Exception:
            return None

    def _extract_text(self, img) -> list[dict]:
        """提取文本块，返回 [{text, bbox, type}]"""
        if self._ocr_name == "easyocr":
            return self._extract_easyocr(img)
        elif self._ocr_name == "pytesseract":
            return self._extract_pytesseract(img)
        else:
            # 无 OCR：返回空
            return []

    def _extract_easyocr(self, img) -> list[dict]:
        """easyocr 提取"""
        from PIL import Image
        import numpy as np
        arr = np.array(img)
        results = self._ocr_engine.readtext(arr)
        blocks = []
        for bbox, text, conf in results:
            # bbox: [[x1,y1],[x2,y1],[x2,y2],[x1,y2]]
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            x, y, w, h = int(min(xs)), int(min(ys)), int(max(xs) - min(xs)), int(max(ys) - min(ys))
            blocks.append({
                "text": text,
                "bbox": (x, y, w, h),
                "confidence": conf,
                "type": self._heuristic_type(text),
            })
        return blocks

    def _extract_pytesseract(self, img) -> list[dict]:
        """pytesseract 提取"""
        from PIL import Image
        data = self._ocr_engine.image_to_data(img, output_type=self._ocr_engine.Output.DICT)
        blocks = []
        n_boxes = len(data["text"])
        for i in range(n_boxes):
            text = data["text"][i].strip()
            if not text:
                continue
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            blocks.append({
                "text": text,
                "bbox": (x, y, w, h),
                "confidence": data["conf"][i] / 100.0,
                "type": self._heuristic_type(text),
            })
        return blocks

    @staticmethod
    def _heuristic_type(text: str) -> str:
        """启发式判断文本类型"""
        lower = text.lower()
        button_hints = ["submit", "login", "sign", "ok", "cancel", "save", "delete",
                        "确认", "提交", "登录", "取消", "保存"]
        input_hints = ["email", "password", "username", "search", "name",
                       "邮箱", "密码", "用户名", "搜索"]
        if any(h in lower for h in button_hints):
            return "button"
        if any(h in lower for h in input_hints):
            return "input_label"
        if text.startswith("http"):
            return "link"
        return "text"
