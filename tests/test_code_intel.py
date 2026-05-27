"""Tests for code intelligence module."""
import tempfile
from pathlib import Path

import pytest

from xagent.core.code_intel.indexer import CodeIndexer, SymbolKind, FileIndex
from xagent.core.code_intel.symbol_graph import SymbolGraph
from xagent.core.code_intel.repo_map import RepoMapBuilder


class TestCodeIndexer:
    def test_detect_language_python(self):
        indexer = CodeIndexer("/tmp")
        assert indexer._detect_language(Path("foo.py")) == "python"
        assert indexer._detect_language(Path("bar.js")) == "javascript"
        assert indexer._detect_language(Path("baz.ts")) == "typescript"
        assert indexer._detect_language(Path("qux.txt")) is None

    def test_index_python_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "sample.py"
            src.write_text("""
class MyClass:
    \"\"\"A sample class.\"\"\"
    def method(self, x: int) -> int:
        return x * 2

def standalone_func(a, b):
    pass
""")
            indexer = CodeIndexer(tmp)
            fidx = indexer.index_file(src)

            assert fidx is not None
            assert fidx.language == "python"
            assert len(fidx.symbols) >= 2

            names = {s.name for s in fidx.symbols}
            assert "MyClass" in names
            assert "method" in names
            assert "standalone_func" in names

    def test_symbol_docstring_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "doc.py"
            src.write_text('''
def greet(name):
    """Say hello to someone."""
    return f"Hello {name}"
''')
            indexer = CodeIndexer(tmp)
            fidx = indexer.index_file(src)
            sym = fidx.find_symbol("greet")
            assert sym is not None
            assert "hello" in sym.docstring.lower()

    def test_index_javascript_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "app.js"
            src.write_text("""
class App {
    init() {
        console.log("ready");
    }
}
function helper() {
    return 42;
}
""")
            indexer = CodeIndexer(tmp)
            fidx = indexer.index_file(src)
            assert fidx is not None
            assert fidx.language == "javascript"
            names = {s.name for s in fidx.symbols}
            assert "App" in names
            assert "init" in names
            assert "helper" in names

    def test_search_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.py").write_text("def alpha(): pass")
            (Path(tmp) / "b.py").write_text("def beta(): pass")
            indexer = CodeIndexer(tmp)
            indexer.index_all()
            results = indexer.search("alpha")
            assert len(results) == 1
            assert results[0].name == "alpha"

    def test_imports_extracted(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "imports.py"
            src.write_text("import os\nfrom pathlib import Path\n")
            indexer = CodeIndexer(tmp)
            fidx = indexer.index_file(src)
            assert "import os" in fidx.imports
            assert "from pathlib import Path" in fidx.imports

    def test_ignore_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "__pycache__").mkdir()
            (Path(tmp) / "__pycache__" / "cached.py").write_text("def cached(): pass")
            (Path(tmp) / "main.py").write_text("def main(): pass")
            indexer = CodeIndexer(tmp)
            indexer.index_all()
            assert "cached" not in {s.name for f in indexer._files.values() for s in f.symbols}
            assert "main" in {s.name for f in indexer._files.values() for s in f.symbols}


class TestRepoMapBuilder:
    def test_build_repo_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "pkg").mkdir()
            (Path(tmp) / "pkg" / "core.py").write_text("class Core:\n    def run(self): pass\n")
            (Path(tmp) / "main.py").write_text("def main(): pass\n")
            indexer = CodeIndexer(tmp)
            indexer.index_all()
            builder = RepoMapBuilder(indexer=indexer)
            repo_map = builder.build()

            assert "pkg/" in repo_map
            assert "core.py" in repo_map
            assert "class Core" in repo_map
            assert "main.py" in repo_map
            assert "def main" in repo_map

    def test_max_symbols_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "many.py"
            src.write_text("\n".join([f"def func_{i}(): pass" for i in range(30)]))
            indexer = CodeIndexer(tmp)
            indexer.index_all()
            builder = RepoMapBuilder(indexer=indexer, max_symbols_per_file=5)
            repo_map = builder.build()
            # 最多 5 个符号
            assert repo_map.count("def func_") <= 5

    def test_truncate_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(50):
                (Path(tmp) / f"file_{i}.py").write_text(f"def func_{i}(): pass\n")
            indexer = CodeIndexer(tmp)
            indexer.index_all()
            builder = RepoMapBuilder(indexer=indexer, max_total_chars=500)
            repo_map = builder.build()
            assert len(repo_map) <= 520  # 500 + "... [truncated]"
            assert "[truncated]" in repo_map

    def test_build_for_project_static(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "app.py").write_text("class App: pass\n")
            repo_map = RepoMapBuilder.build_for_project(tmp, max_total_chars=2000)
            assert "app.py" in repo_map
            assert "class App" in repo_map


class TestSymbolGraph:
    def test_build_and_impact(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 用 docstring 建立调用关系（当前启发式实现）
            (Path(tmp) / "a.py").write_text('''
def auth_middleware():
    """Called by api_handler and another_handler."""
    pass

def api_handler():
    auth_middleware()
''')
            (Path(tmp) / "b.py").write_text('''
def another_handler():
    auth_middleware()
''')
            indexer = CodeIndexer(tmp)
            indexer.index_all()
            graph = SymbolGraph(indexer)
            graph.build()

            # auth_middleware 被修改的影响面
            affected = graph.impact("auth_middleware")
            # 通过 docstring 中的名称匹配建立关系
            assert len(affected) >= 1

    def test_top_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "mod.py").write_text("""
def core_util():
    pass

def wrapper():
    core_util()
""")
            indexer = CodeIndexer(tmp)
            indexer.index_all()
            graph = SymbolGraph(indexer)
            graph.build()
            top = graph.top_symbols(5)
            assert len(top) <= 5
            # core_util 被调用，应该有更高分数
            names = [n.split("::")[-1] for n, _ in top]
            assert "core_util" in names

    def test_empty_indexer(self):
        graph = SymbolGraph()
        assert graph.build() == {}
        assert graph.impact("anything") == []
        assert graph.top_symbols() == []
