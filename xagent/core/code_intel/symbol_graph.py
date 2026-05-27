"""符号依赖图

基于索引结果构建符号间的调用/引用关系图。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from .indexer import CodeIndexer, Symbol, SymbolKind


@dataclass
class SymbolNode:
    """图中的符号节点"""
    symbol: Symbol
    callers: list[str] = field(default_factory=list)   # 调用此符号的符号名
    callees: list[str] = field(default_factory=list)   # 此符号调用的符号名
    score: float = 1.0  # PageRank 分数


class SymbolGraph:
    """
    符号依赖图。

    用法:
        graph = SymbolGraph(indexer)
        graph.build()
        affected = graph.impact("auth.middleware")  # 修改影响面
    """

    def __init__(self, indexer: Optional[CodeIndexer] = None):
        self.indexer = indexer
        self._nodes: dict[str, SymbolNode] = {}

    def build(self) -> dict[str, SymbolNode]:
        """从索引结果构建符号图"""
        if self.indexer is None:
            return {}

        # 1. 创建节点
        for sym in self.indexer.list_all_symbols():
            key = self._key(sym)
            self._nodes[key] = SymbolNode(symbol=sym)

        # 2. 建立边（基于简单文本匹配——精确分析需要更复杂的静态分析）
        # TODO: 未来可用 tree-sitter call_expression 精确提取
        for key, node in self._nodes.items():
            sym = node.symbol
            if sym.kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
                continue
            # 启发式：如果 A 的 docstring 提到 B，认为 B 调用了 A
            # 即 A 是 callee，B 是 caller
            for other_key, other_node in self._nodes.items():
                if other_key == key:
                    continue
                if other_node.symbol.name in (sym.docstring or ""):
                    # other_node (B) 调用了 node (A)
                    node.callers.append(other_key)
                    other_node.callees.append(key)

        # 3. 计算 PageRank（简化版）
        self._compute_pagerank()
        return self._nodes

    def impact(self, symbol_name: str) -> list[str]:
        """
        分析修改某个符号的影响面。
        返回所有直接和间接依赖此符号的符号名列表。
        """
        key = symbol_name
        if key not in self._nodes:
            # 尝试模糊匹配
            for k, n in self._nodes.items():
                if n.symbol.name == symbol_name or n.symbol.full_name() == symbol_name:
                    key = k
                    break

        if key not in self._nodes:
            return []

        # BFS 找所有调用者（反向传播）
        affected = set()
        queue = [key]
        visited = {key}
        while queue:
            current = queue.pop(0)
            affected.add(current)
            node = self._nodes.get(current)
            if not node:
                continue
            for caller in node.callers:
                if caller not in visited:
                    visited.add(caller)
                    queue.append(caller)

        affected.discard(key)
        return sorted(affected)

    def top_symbols(self, n: int = 10) -> list[tuple[str, float]]:
        """返回 PageRank 最高的 N 个符号"""
        sorted_nodes = sorted(
            self._nodes.items(),
            key=lambda x: x[1].score,
            reverse=True,
        )
        return [(k, v.score) for k, v in sorted_nodes[:n]]

    def _compute_pagerank(self, iterations: int = 10, damping: float = 0.85):
        """简化 PageRank"""
        if not self._nodes:
            return
        n = len(self._nodes)
        for _ in range(iterations):
            new_scores = {}
            for key, node in self._nodes.items():
                rank = (1 - damping) / n
                for caller in node.callers:
                    caller_node = self._nodes.get(caller)
                    if caller_node and caller_node.callees:
                        rank += damping * caller_node.score / len(caller_node.callees)
                new_scores[key] = rank
            for key, score in new_scores.items():
                self._nodes[key].score = score

    @staticmethod
    def _key(sym: Symbol) -> str:
        return f"{sym.file_path}::{sym.full_name()}"
