"""修改影响面分析器

分析修改某个符号会影响哪些代码位置，帮助 Agent 做出全面的编辑计划。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from .indexer import CodeIndexer, Symbol
from .symbol_graph import SymbolGraph


@dataclass
class ImpactReport:
    """修改影响面报告"""
    target_symbol: str
    target_file: str
    direct_callers: list[Symbol] = field(default_factory=list)   # 直接调用者
    indirect_callers: list[Symbol] = field(default_factory=list)  # 间接调用者
    files_affected: list[str] = field(default_factory=list)       # 受影响的文件
    test_files: list[str] = field(default_factory=list)           # 可能相关的测试文件
    estimated_edits: int = 0

    def to_markdown(self) -> str:
        lines = [
            f"## Impact Analysis: `{self.target_symbol}`",
            f"**File:** {self.target_file}",
            "",
            f"- **Direct callers:** {len(self.direct_callers)}",
            f"- **Indirect callers:** {len(self.indirect_callers)}",
            f"- **Files affected:** {len(self.files_affected)}",
            f"- **Estimated edits:** {self.estimated_edits}",
            "",
        ]
        if self.direct_callers:
            lines.append("### Direct Callers")
            for sym in self.direct_callers:
                lines.append(f"- `{sym.full_name()}` ({sym.file_path}:{sym.line_start})")
            lines.append("")
        if self.files_affected:
            lines.append("### Files to Update")
            for f in self.files_affected:
                lines.append(f"- `{f}`")
            lines.append("")
        return "\n".join(lines)


class ChangeImpactAnalyzer:
    """
    修改影响面分析器。

    用法:
        analyzer = ChangeImpactAnalyzer(indexer)
        report = analyzer.analyze("auth_middleware")
        print(report.to_markdown())
    """

    def __init__(self, indexer: Optional[CodeIndexer] = None):
        self.indexer = indexer
        self._graph: Optional[SymbolGraph] = None

    def analyze(self, symbol_name: str) -> ImpactReport:
        """分析修改某个符号的影响面"""
        if self.indexer is None:
            return ImpactReport(target_symbol=symbol_name, target_file="")

        # 找到目标符号
        target = self._find_symbol(symbol_name)
        if target is None:
            return ImpactReport(target_symbol=symbol_name, target_file="")

        # 构建或复用符号图
        if self._graph is None:
            self._graph = SymbolGraph(self.indexer)
            self._graph.build()

        # 获取影响链
        affected_keys = self._graph.impact(symbol_name)

        # 解析为 Symbol 对象
        direct = []
        indirect = []
        files = set()
        tests = []

        for key in affected_keys:
            node = self._graph._nodes.get(key)
            if not node:
                continue
            sym = node.symbol
            if sym.file_path == target.file_path and sym.name == target.name:
                continue

            # 直接调用者：距离 1
            if target.full_name() in [c.split("::")[-1] for c in node.symbol.docstring.split()]:
                direct.append(sym)
            else:
                indirect.append(sym)

            files.add(sym.file_path)

            # 启发式：识别测试文件
            if self._is_test_file(sym.file_path):
                tests.append(sym.file_path)

        # 总是包含目标文件
        files.add(target.file_path)

        return ImpactReport(
            target_symbol=target.full_name(),
            target_file=target.file_path,
            direct_callers=direct,
            indirect_callers=indirect,
            files_affected=sorted(files),
            test_files=sorted(set(tests)),
            estimated_edits=len(files),
        )

    def _find_symbol(self, name: str) -> Optional[Symbol]:
        """模糊查找符号"""
        # 1. 精确匹配
        for sym in self.indexer.list_all_symbols():
            if sym.name == name or sym.full_name() == name:
                return sym
        # 2. 部分匹配
        for sym in self.indexer.list_all_symbols():
            if name.lower() in sym.name.lower():
                return sym
        return None

    @staticmethod
    def _is_test_file(path: str) -> bool:
        """启发式判断是否为测试文件"""
        lower = path.lower()
        return any(marker in lower for marker in [
            "test_", "_test.", "tests/", "/tests/",
            "spec.", "_spec.", ".test.",
        ])
