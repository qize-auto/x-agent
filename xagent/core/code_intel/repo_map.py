"""仓库地图生成器

将索引结果转为 LLM 友好的文本摘要，帮助 Agent 快速理解项目结构。
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

from .indexer import CodeIndexer, SymbolKind


class RepoMapBuilder:
    """
    构建仓库结构地图。

    输出格式示例:
    ```
    xagent/
    ├── core/
    │   ├── agent_loop.py
    │   │   ├── class AgentLoop
    │   │   │   ├── def run(self, user_input)
    │   │   │   └── def run_task(self, goal)
    │   │   └── def _is_simple_goal(goal)
    │   └── cache_loop.py
    │       ├── class CacheFirstLoop
    │       └── def run(user_input)
    └── tools/
        └── filesystem.py
            ├── def read_file(path)
            └── def write_file(path, content)
    ```
    """

    def __init__(self, indexer: Optional[CodeIndexer] = None,
                 max_total_chars: int = 8000,
                 max_symbols_per_file: int = 15):
        self.indexer = indexer
        self.max_total_chars = max_total_chars
        self.max_symbols_per_file = max_symbols_per_file

    def build(self, project_root: str = "") -> str:
        """生成仓库地图字符串"""
        if self.indexer is None:
            return ""

        files = list(self.indexer._files.values())
        if not files:
            return ""

        # 按路径排序，构建树形结构
        root = Path(project_root) if project_root else Path(self.indexer.project_root)
        tree = self._build_tree(files, root)
        lines = self._render_tree(tree, root, "")
        result = "\n".join(lines)

        # 截断保护
        if len(result) > self.max_total_chars:
            result = result[:self.max_total_chars] + "\n... [truncated]"
        return result

    def _build_tree(self, files, root: Path) -> dict:
        """构建嵌套字典树"""
        tree: dict = {}
        for fidx in files:
            rel = Path(fidx.path).relative_to(root)
            parts = rel.parts
            node = tree
            for part in parts[:-1]:
                if part not in node:
                    node[part] = {"__type__": "dir", "__children__": {}}
                node = node[part]["__children__"]
            # 文件节点
            fname = parts[-1]
            symbols = [s for s in fidx.symbols if s.kind in (
                SymbolKind.FUNCTION, SymbolKind.CLASS, SymbolKind.METHOD
            )]
            # 限制每个文件的符号数量
            if len(symbols) > self.max_symbols_per_file:
                symbols = symbols[:self.max_symbols_per_file]
            node[fname] = {
                "__type__": "file",
                "symbols": symbols,
            }
        return tree

    def _render_tree(self, tree: dict, root: Path, prefix: str) -> list[str]:
        """渲染树形结构为文本行"""
        lines = []
        items = sorted(tree.items(), key=lambda x: (x[1].get("__type__") != "dir", x[0].lower()))

        for i, (name, node) in enumerate(items):
            is_last = i == len(items) - 1
            connector = "└── " if is_last else "├── "
            child_prefix = prefix + ("    " if is_last else "│   ")

            if node.get("__type__") == "dir":
                lines.append(f"{prefix}{connector}{name}/")
                children = node.get("__children__", {})
                lines.extend(self._render_tree(children, root, child_prefix))
            else:
                lines.append(f"{prefix}{connector}{name}")
                symbols = node.get("symbols", [])
                for j, sym in enumerate(symbols):
                    sym_last = j == len(symbols) - 1
                    sym_connector = "└── " if sym_last else "├── "
                    sig = sym.signature[:40] if sym.signature else ""
                    if sig:
                        sig = f"({sig})"
                    kind_label = {
                        SymbolKind.CLASS: "class",
                        SymbolKind.FUNCTION: "def",
                        SymbolKind.METHOD: "def",
                    }.get(sym.kind, "")
                    lines.append(f"{child_prefix}{sym_connector}{kind_label} {sym.name}{sig}")
        return lines

    @staticmethod
    def build_for_project(project_root: str, **kwargs) -> str:
        """便捷方法：一键生成仓库地图"""
        indexer = CodeIndexer(project_root)
        indexer.index_all()
        builder = RepoMapBuilder(indexer=indexer, **kwargs)
        return builder.build()
