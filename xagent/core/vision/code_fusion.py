"""Visual-Code Fusion — 视觉与代码的关联层

将 UI 截图/描述映射到代码库中的具体位置。
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .base import UIPerception, UIElement
from ..code_intel.indexer import CodeIndexer


@dataclass
class CodeLocation:
    """代码定位结果"""
    file_path: str
    line_start: int
    line_end: int = 0
    confidence: float = 0.0
    snippet: str = ""
    match_reason: str = ""


class VisualCodeFusion:
    """
    视觉-代码融合分析器。

    场景：
    1. UI bug 报告：用户发截图 "这个按钮点不了"
       → 定位到对应的 React/Vue/HTML 组件
    2. 设计稿到代码：用户发 Figma 截图 "按这个样式改"
       → 找到对应样式文件
    3. 界面验证：截图对比代码预期状态
    """

    def __init__(self, indexer: Optional[CodeIndexer] = None):
        self.indexer = indexer

    def trace_ui_to_code(self, perception: UIPerception, keyword: str = "") -> list[CodeLocation]:
        """
        将 UI 感知结果映射到代码位置。

        Args:
            perception: UI 感知结果
            keyword: 用户指定的关键词（如按钮文字）
        """
        if self.indexer is None:
            return []

        # 1. 从 perception 提取关键词
        search_terms = self._extract_search_terms(perception, keyword)
        if not search_terms:
            return []

        # 2. 在代码库中搜索
        locations = []
        for term in search_terms:
            for sym in self.indexer.search(term):
                loc = CodeLocation(
                    file_path=sym.file_path,
                    line_start=sym.line_start,
                    line_end=sym.line_end,
                    confidence=0.6,
                    snippet=sym.name,
                    match_reason=f"Symbol name matches '{term}'",
                )
                locations.append(loc)

            # 3. 文本搜索（文件名、导入等）
            for fidx in self.indexer._files.values():
                for sym in fidx.symbols:
                    if term.lower() in sym.name.lower():
                        loc = CodeLocation(
                            file_path=sym.file_path,
                            line_start=sym.line_start,
                            line_end=sym.line_end,
                            confidence=0.5,
                            snippet=sym.name,
                            match_reason=f"Partial match '{term}'",
                        )
                        if loc not in locations:
                            locations.append(loc)

        # 4. 语义增强：根据元素类型提升置信度
        locations = self._boost_by_element_type(locations, perception)

        # 去重并按置信度排序
        seen = set()
        unique = []
        for loc in sorted(locations, key=lambda x: x.confidence, reverse=True):
            key = (loc.file_path, loc.line_start)
            if key not in seen:
                seen.add(key)
                unique.append(loc)

        return unique[:10]

    def find_style_for_element(self, perception: UIPerception, element_id: str) -> list[CodeLocation]:
        """找到某个 UI 元素对应的样式代码"""
        if self.indexer is None:
            return []

        elem = None
        for e in perception.elements:
            if e.element_id == element_id:
                elem = e
                break
        if elem is None:
            return []

        # 启发式：在 CSS/SCSS/LESS/样式文件中搜索类名或颜色值
        search_terms = []
        if elem.label:
            # 将 label 转为可能的 className（camelCase / kebab-case）
            label = elem.label.replace(" ", "").lower()
            search_terms.append(label)
            search_terms.append(label.replace("-", ""))
        if elem.text:
            search_terms.append(elem.text.lower())

        # 添加可能的 CSS 属性值（颜色、尺寸等）
        if elem.bbox and any(elem.bbox):
            # bbox 可能暗示尺寸
            pass

        locations = []
        style_exts = {".css", ".scss", ".less", ".sass", ".styl"}

        # 收集所有样式文件路径（包括未被索引的）
        style_files = set()
        if self.indexer and self.indexer.project_root:
            import os
            for root, _, files in os.walk(self.indexer.project_root):
                for fname in files:
                    if any(fname.endswith(ext) for ext in style_exts):
                        style_files.add(os.path.join(root, fname))
        # 也包含已被索引的文件
        for fidx in getattr(self.indexer, "_files", {}).values():
            path = getattr(fidx, "path", "")
            if any(path.endswith(ext) for ext in style_exts):
                style_files.add(path)

        for path in style_files:
            try:
                content = open(path, "r", encoding="utf-8").read()
                lines = content.splitlines()
                for i, line in enumerate(lines, 1):
                    for term in search_terms:
                        if term in line.lower():
                            # 找到选择器上下文（向前查找选择器）
                            selector = self._find_selector(lines, i - 1)
                            locations.append(CodeLocation(
                                file_path=path,
                                line_start=i,
                                confidence=0.5,
                                snippet=line.strip()[:80],
                                match_reason=f"Style match for '{term}': {selector}",
                            ))
                            break
            except Exception:
                continue

        # 去重
        seen = set()
        unique = []
        for loc in sorted(locations, key=lambda x: x.confidence, reverse=True):
            key = (loc.file_path, loc.line_start)
            if key not in seen:
                seen.add(key)
                unique.append(loc)
        return unique[:10]

    def match_by_layout(self, perception: UIPerception) -> list[CodeLocation]:
        """
        根据 UI 布局结构（元素类型和层级）匹配代码组件。
        例如：一个包含 [input, input, button] 的表单 → 匹配 LoginForm / SignUpForm 组件。
        """
        if self.indexer is None or not perception.elements:
            return []

        # 提取布局指纹：元素类型序列
        layout_fingerprint = [e.element_type for e in perception.elements[:10]]
        layout_str = " > ".join(layout_fingerprint)

        # 启发式：搜索包含这些元素类型的组件（React/Vue/JS 组件）
        locations = []
        component_exts = {".jsx", ".tsx", ".vue", ".svelte", ".js"}

        # 收集所有组件文件路径（包括未被索引的）
        component_files = {}
        if self.indexer and self.indexer.project_root:
            import os
            for root, _, files in os.walk(self.indexer.project_root):
                for fname in files:
                    if any(fname.endswith(ext) for ext in component_exts):
                        fpath = os.path.join(root, fname)
                        component_files[fpath] = None  # 尚未读取内容
        # 也包含已被索引的文件
        for fidx in getattr(self.indexer, "_files", {}).values():
            path = getattr(fidx, "path", "")
            if any(path.endswith(ext) for ext in component_exts):
                component_files[path] = fidx

        for path, fidx in component_files.items():
            try:
                content = open(path, "r", encoding="utf-8").read()
                # 计算元素类型在文件中的出现频率
                score = 0
                for etype in layout_fingerprint:
                    if etype in content.lower():
                        score += 1
                if score >= max(2, len(layout_fingerprint) * 0.3):
                    # 找到最匹配的符号（通常是组件名）
                    best_sym = None
                    if fidx:
                        for sym in fidx.symbols:
                            if sym.symbol_type in ("class", "function"):
                                best_sym = sym
                                break
                    if best_sym:
                        line_start = best_sym.line_start
                        snippet = best_sym.name
                    else:
                        line_start = 1
                        snippet = Path(path).stem
                    locations.append(CodeLocation(
                        file_path=path,
                        line_start=line_start,
                        confidence=min(0.9, 0.4 + score * 0.1),
                        snippet=snippet,
                        match_reason=f"Layout match: {layout_str}",
                    ))
            except Exception as e:
                import traceback
                traceback.print_exc()
                continue

        return sorted(locations, key=lambda x: x.confidence, reverse=True)[:5]

    @staticmethod
    def _extract_search_terms(perception: UIPerception, keyword: str) -> list[str]:
        """从感知结果提取搜索关键词"""
        terms = []
        if keyword:
            terms.append(keyword)
        for elem in perception.elements:
            if elem.label and len(elem.label) > 2:
                terms.append(elem.label)
            if elem.text and elem.text != elem.label and len(elem.text) > 2:
                terms.append(elem.text)
        # 从 raw_text 提取大写开头的词（可能是组件名）
        import re
        if perception.raw_text:
            candidates = re.findall(r'\b[A-Z][a-zA-Z]+\b', perception.raw_text)
            terms.extend(candidates[:5])
        return list(dict.fromkeys(terms))  # 去重保序

    @staticmethod
    def _find_selector(lines: list[str], line_idx: int) -> str:
        """从 CSS 文件中找到某一行所属的选择器"""
        # 向前查找最近的选择器行（以 { 结尾或纯选择器）
        for i in range(line_idx, max(-1, line_idx - 20), -1):
            stripped = lines[i].strip()
            if stripped.endswith("{") or (
                stripped and not stripped.startswith("//") and not stripped.startswith("/*")
                and ":" not in stripped and ";" not in stripped
            ):
                return stripped[:60]
        return ""

    def _boost_by_element_type(self, locations: list[CodeLocation],
                               perception: UIPerception) -> list[CodeLocation]:
        """根据 UI 元素类型提升代码位置置信度"""
        has_button = any(e.element_type == "button" for e in perception.elements)
        has_input = any(e.element_type == "input" for e in perception.elements)
        has_link = any(e.element_type == "link" for e in perception.elements)

        for loc in locations:
            snippet_lower = loc.snippet.lower()
            if has_button and ("button" in snippet_lower or "btn" in snippet_lower):
                loc.confidence = min(1.0, loc.confidence + 0.15)
            if has_input and ("input" in snippet_lower or "field" in snippet_lower):
                loc.confidence = min(1.0, loc.confidence + 0.15)
            if has_link and ("link" in snippet_lower or "a " in snippet_lower):
                loc.confidence = min(1.0, loc.confidence + 0.15)

        return locations
