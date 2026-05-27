"""代码索引器

基于 tree-sitter 的多语言 AST 解析 + jedi Python 增强。
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional

from tree_sitter import Language, Parser, Node


class SymbolKind(Enum):
    FUNCTION = auto()
    CLASS = auto()
    METHOD = auto()
    VARIABLE = auto()
    IMPORT = auto()
    MODULE = auto()
    PROPERTY = auto()
    UNKNOWN = auto()


@dataclass
class Symbol:
    """代码符号"""
    name: str
    kind: SymbolKind
    file_path: str
    line_start: int
    line_end: int
    signature: str = ""         # 函数签名 / 类继承列表
    docstring: str = ""         # 文档字符串
    parent: Optional[str] = None  # 父符号名（如类中的方法）
    children: list[str] = field(default_factory=list)

    def full_name(self) -> str:
        if self.parent:
            return f"{self.parent}.{self.name}"
        return self.name


@dataclass
class FileIndex:
    """单个文件的索引结果"""
    path: str
    language: str
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)

    def find_symbol(self, name: str) -> Optional[Symbol]:
        for s in self.symbols:
            if s.name == name or s.full_name() == name:
                return s
        return None


class CodeIndexer:
    """
    代码索引器。

    用法:
        indexer = CodeIndexer(project_root="/path/to/project")
        indexer.index_all()
        for sym in indexer.search("def read_file"):
            print(sym.file_path, sym.line_start)
    """

    # 语言 → 文件扩展名 → tree-sitter language
    LANGUAGE_MAP = {
        "python": {
            "exts": {".py"},
            "parser_factory": lambda: CodeIndexer._load_parser("python"),
        },
        "javascript": {
            "exts": {".js", ".mjs", ".cjs"},
            "parser_factory": lambda: CodeIndexer._load_parser("javascript"),
        },
        "typescript": {
            "exts": {".ts", ".tsx"},
            "parser_factory": lambda: CodeIndexer._load_parser("typescript"),
        },
    }

    # 忽略模式
    DEFAULT_IGNORE = {
        ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
        "node_modules", "dist", "build", ".mypy_cache", ".tox",
        ".eggs", "*.egg-info", ".coverage", "htmlcov",
    }

    def __init__(self, project_root: str, ignore_patterns: set[str] = None, max_files: int | None = None):
        self.project_root = Path(project_root).resolve()
        self.ignore_patterns = ignore_patterns or set(self.DEFAULT_IGNORE)
        self.max_files = max_files  # 硬件档位限制的最大文件数
        self._files: dict[str, FileIndex] = {}
        self._parsers: dict[str, Parser] = {}
        self._jedi_available = self._check_jedi()

    # ── 公共 API ──

    def index_all(self, max_files: int | None = None) -> dict[str, FileIndex]:
        """索引整个项目。max_files 覆盖构造时的限制。"""
        self._files = {}
        limit = max_files if max_files is not None else self.max_files
        count = 0
        for file_path in self._walk_files():
            if limit is not None and count >= limit:
                break
            try:
                file_index = self.index_file(file_path)
                if file_index:
                    self._files[str(file_path)] = file_index
                    count += 1
            except Exception:
                pass
        return self._files

    def index_file(self, file_path: str | Path) -> Optional[FileIndex]:
        """索引单个文件"""
        path = Path(file_path)
        lang = self._detect_language(path)
        if not lang:
            return None

        source = path.read_text(encoding="utf-8", errors="replace")
        parser = self._get_parser(lang)
        if not parser:
            return None

        tree = parser.parse(source.encode("utf-8"))
        symbols, imports = self._extract_symbols(tree.root_node, source, str(path), lang)

        # Python 增强：用 jedi 做类型推断
        if lang == "python" and self._jedi_available:
            symbols = self._enhance_python_with_jedi(symbols, source, str(path))

        return FileIndex(
            path=str(path),
            language=lang,
            symbols=symbols,
            imports=imports,
        )

    def search(self, query: str) -> list[Symbol]:
        """简单文本搜索符号名"""
        results = []
        q = query.lower()
        for fidx in self._files.values():
            for sym in fidx.symbols:
                if q in sym.name.lower() or q in sym.full_name().lower():
                    results.append(sym)
        return results

    def get_file(self, path: str) -> Optional[FileIndex]:
        return self._files.get(str(Path(path).resolve()))

    def list_all_symbols(self) -> list[Symbol]:
        """返回所有已索引的符号"""
        symbols = []
        for fidx in self._files.values():
            symbols.extend(fidx.symbols)
        return symbols

    # ── 内部实现 ──

    def _walk_files(self):
        """遍历项目文件"""
        for root, dirs, files in os.walk(self.project_root):
            # 过滤忽略的目录
            dirs[:] = [d for d in dirs if d not in self.ignore_patterns
                       and not any(d.endswith(pat.strip("*")) for pat in self.ignore_patterns if "*" in pat)]
            for fname in files:
                if any(fname.endswith(ext) for lang in self.LANGUAGE_MAP.values() for ext in lang["exts"]):
                    yield Path(root) / fname

    def _detect_language(self, path: Path) -> Optional[str]:
        for lang, cfg in self.LANGUAGE_MAP.items():
            if path.suffix in cfg["exts"]:
                return lang
        return None

    @staticmethod
    def _load_parser(lang: str) -> Optional[Parser]:
        """动态加载 tree-sitter parser"""
        try:
            if lang == "python":
                from tree_sitter_python import language as py_lang
                return Parser(Language(py_lang()))
            elif lang == "javascript":
                from tree_sitter_javascript import language as js_lang
                return Parser(Language(js_lang()))
            elif lang == "typescript":
                from tree_sitter_typescript import language as ts_lang
                return Parser(Language(ts_lang()))
        except Exception:
            pass
        return None

    def _get_parser(self, lang: str) -> Optional[Parser]:
        if lang not in self._parsers:
            factory = self.LANGUAGE_MAP.get(lang, {}).get("parser_factory")
            if factory:
                self._parsers[lang] = factory()
        return self._parsers.get(lang)

    @staticmethod
    def _check_jedi() -> bool:
        try:
            import jedi
            return True
        except Exception:
            return False

    def _extract_symbols(self, root: Node, source: str, file_path: str, lang: str) -> tuple[list[Symbol], list[str]]:
        """从 AST 提取符号和导入"""
        symbols = []
        imports = []
        source_lines = source.splitlines()

        def visit(node: Node, parent_name: str = ""):
            kind = SymbolKind.UNKNOWN
            name = ""
            signature = ""

            if lang == "python":
                if node.type == "function_definition":
                    kind = SymbolKind.METHOD if parent_name else SymbolKind.FUNCTION
                    name_node = node.child_by_field_name("name")
                    name = self._node_text(name_node, source) if name_node else ""
                    params_node = node.child_by_field_name("parameters")
                    if params_node:
                        signature = self._node_text(params_node, source)
                elif node.type == "class_definition":
                    kind = SymbolKind.CLASS
                    name_node = node.child_by_field_name("name")
                    name = self._node_text(name_node, source) if name_node else ""
                    # 提取继承列表
                    for child in node.children:
                        if child.type == "argument_list":
                            signature = self._node_text(child, source)
                            break
                elif node.type == "import_statement" or node.type == "import_from_statement":
                    imports.append(self._node_text(node, source).strip())
                    return  # 导入不是符号

            elif lang in ("javascript", "typescript"):
                if node.type in ("function_declaration", "arrow_function", "function"):
                    kind = SymbolKind.FUNCTION
                    name_node = node.child_by_field_name("name")
                    name = self._node_text(name_node, source) if name_node else ""
                elif node.type == "class_declaration":
                    kind = SymbolKind.CLASS
                    name_node = node.child_by_field_name("name")
                    name = self._node_text(name_node, source) if name_node else ""
                elif node.type == "method_definition":
                    kind = SymbolKind.METHOD
                    name_node = node.child_by_field_name("name")
                    name = self._node_text(name_node, source) if name_node else ""
                elif node.type == "import_statement" or node.type == "import_declaration":
                    imports.append(self._node_text(node, source).strip())
                    return

            if name and kind != SymbolKind.UNKNOWN:
                docstring = ""
                # 尝试提取 docstring（Python）
                if lang == "python" and kind in (SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.CLASS):
                    docstring = self._extract_docstring(node, source)

                sym = Symbol(
                    name=name,
                    kind=kind,
                    file_path=file_path,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    signature=signature,
                    docstring=docstring,
                    parent=parent_name if parent_name else None,
                )
                symbols.append(sym)

                # 递归访问子节点（类中的方法等）
                current_parent = parent_name
                if kind == SymbolKind.CLASS:
                    current_parent = name
                for child in node.children:
                    visit(child, current_parent)
            else:
                for child in node.children:
                    visit(child, parent_name)

        visit(root)
        return symbols, imports

    @staticmethod
    def _node_text(node: Optional[Node], source: str) -> str:
        if node is None:
            return ""
        return source[node.start_byte:node.end_byte]

    @staticmethod
    def _extract_docstring(node: Node, source: str) -> str:
        """提取 Python 函数/类的 docstring"""
        for child in node.children:
            if child.type == "block":
                for stmt in child.children:
                    if stmt.type == "expression_statement":
                        for inner in stmt.children:
                            if inner.type == "string":
                                text = source[inner.start_byte:inner.end_byte]
                                return text.strip("\"'\n ")
                break
        return ""

    def _enhance_python_with_jedi(self, symbols: list[Symbol], source: str, file_path: str) -> list[Symbol]:
        """用 jedi 增强 Python 符号信息"""
        try:
            import jedi
            script = jedi.Script(source, path=file_path)
            # TODO: 用 jedi 做更精确的类型推断和引用分析
            # 当前版本保持简单，后续可扩展
        except Exception:
            pass
        return symbols
