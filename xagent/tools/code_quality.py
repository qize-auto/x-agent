"""
代码质量工具
============
AST 分析、Lint、测试运行、代码格式化。

可选依赖:
    - tree-sitter (高级 AST 分析)
    - ruff (快速 lint/format)
    - pytest (测试运行)

降级方案:
    - Python 内置 ast 模块
    - py_compile 语法检查
    - 正则提取函数/类名
"""
from __future__ import annotations
import ast
import os
import py_compile
import re
import subprocess
import sys
from pathlib import Path


def register_code_quality_tools(registry):
    """注册代码质量相关工具"""

    def analyze_code(path: str) -> str:
        """分析代码结构：提取函数、类、导入、复杂度"""
        p = Path(path).expanduser()
        if not p.exists():
            return f"[错误] 文件不存在: {path}"

        ext = p.suffix.lower()
        content = p.read_text(encoding="utf-8", errors="ignore")

        if ext == ".py":
            return _analyze_python(content, str(p))
        elif ext in (".js", ".ts", ".jsx", ".tsx"):
            return _analyze_js_ts(content, str(p))
        elif ext in (".go", ".rs", ".java", ".cpp", ".c", ".h"):
            return _analyze_generic(content, str(p), ext)
        else:
            return _analyze_generic(content, str(p), ext)

    def _analyze_python(source: str, path: str) -> str:
        """Python AST 分析"""
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return f"[语法错误] 行{e.lineno}: {e.msg}"

        classes = []
        functions = []
        imports = []
        complexity = 0

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
                classes.append(f"class {node.name}(methods={len(methods)})")
            elif isinstance(node, ast.FunctionDef):
                functions.append(f"def {node.name}(args={len(node.args.args)})")
                complexity += _calc_cyclomatic(node)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.append(ast.unparse(node) if hasattr(ast, "unparse") else "import ...")

        lines = [
            f"文件: {path}",
            f"总行数: {len(source.splitlines())}",
            f"类: {len(classes)}",
            f"  {', '.join(classes[:10])}{'...' if len(classes) > 10 else ''}",
            f"函数: {len(functions)}",
            f"  {', '.join(functions[:10])}{'...' if len(functions) > 10 else ''}",
            f"导入: {len(imports)}",
            f"圈复杂度(估算): {complexity}",
        ]
        return "\n".join(lines)

    def _calc_cyclomatic(node: ast.AST) -> int:
        """简单圈复杂度估算"""
        complexity = 1
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.While, ast.For, ast.ExceptHandler,
                                  ast.With, ast.Assert, ast.comprehension)):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1
        return complexity

    def _analyze_js_ts(source: str, path: str) -> str:
        """JS/TS 简单正则分析"""
        classes = re.findall(r'(?:class|interface)\s+(\w+)', source)
        functions = re.findall(r'(?:function|const|let|var)\s+(\w+)\s*[=:]\s*(?:async\s*)?\(', source)
        functions += re.findall(r'(?:async\s+)?(\w+)\s*\([^)]*\)\s*[{=]', source)
        imports = re.findall(r'import\s+.*?from\s+[\'"](.+?)[\'"]', source)
        lines = [
            f"文件: {path} (JS/TS)",
            f"总行数: {len(source.splitlines())}",
            f"类/接口: {len(classes)} — {', '.join(classes[:10])}",
            f"函数: {len(functions)} — {', '.join(functions[:10])}",
            f"导入: {len(imports)} — {', '.join(imports[:10])}",
        ]
        return "\n".join(lines)

    def _analyze_generic(source: str, path: str, ext: str) -> str:
        """通用代码分析（正则提取）"""
        lines_count = len(source.splitlines())
        # 尝试提取函数定义
        func_pattern = r'(?:func|def|fn|void|int|String|bool)\s+(\w+)\s*\('
        functions = re.findall(func_pattern, source)
        # 尝试提取类定义
        class_pattern = r'(?:class|struct|interface)\s+(\w+)'
        classes = re.findall(class_pattern, source)
        return (
            f"文件: {path} ({ext})\n"
            f"总行数: {lines_count}\n"
            f"类/结构体: {len(classes)} — {', '.join(classes[:10])}\n"
            f"函数: {len(functions)} — {', '.join(functions[:10])}"
        )

    def lint_code(path: str, fix: bool = False) -> str:
        """运行 Linter，返回诊断信息"""
        p = Path(path).expanduser()
        if not p.exists():
            return f"[错误] 文件不存在: {path}"

        ext = p.suffix.lower()
        results = []

        # Python: 优先 ruff，其次 flake8，最后 py_compile
        if ext == ".py":
            if _has_tool("ruff"):
                cmd = ["ruff", "check", str(p)]
                if fix:
                    cmd.append("--fix")
                results.append(_run_cmd(cmd, cwd=str(p.parent)))
            elif _has_tool("flake8"):
                results.append(_run_cmd(["flake8", str(p)], cwd=str(p.parent)))
            else:
                # 内置语法检查
                try:
                    py_compile.compile(str(p), doraise=True)
                    results.append("py_compile: 语法检查通过")
                except py_compile.PyCompileError as e:
                    results.append(f"py_compile: {e}")

        # JS/TS: eslint
        elif ext in (".js", ".ts", ".jsx", ".tsx"):
            if _has_tool("eslint"):
                cmd = ["eslint", str(p)]
                if fix:
                    cmd.append("--fix")
                results.append(_run_cmd(cmd, cwd=str(p.parent)))
            else:
                results.append("未安装 eslint，跳过 lint")

        # Go
        elif ext == ".go":
            if _has_tool("go"):
                results.append(_run_cmd(["go", "vet", str(p)], cwd=str(p.parent)))
            else:
                results.append("未安装 go，跳过 vet")

        # Rust
        elif ext == ".rs":
            if _has_tool("cargo"):
                results.append(_run_cmd(["cargo", "check"], cwd=str(p.parent)))
            else:
                results.append("未安装 cargo，跳过 check")

        else:
            results.append(f"暂不支持 {ext} 文件的 lint")

        return "\n".join(results) if results else "无诊断信息"

    def format_code(path: str) -> str:
        """格式化代码"""
        p = Path(path).expanduser()
        if not p.exists():
            return f"[错误] 文件不存在: {path}"

        ext = p.suffix.lower()

        if ext == ".py":
            if _has_tool("ruff"):
                return _run_cmd(["ruff", "format", str(p)], cwd=str(p.parent))
            elif _has_tool("black"):
                return _run_cmd(["black", str(p)], cwd=str(p.parent))
            else:
                return "未安装 ruff/black，跳过格式化"

        elif ext in (".js", ".ts", ".jsx", ".tsx"):
            if _has_tool("prettier"):
                return _run_cmd(["prettier", "--write", str(p)], cwd=str(p.parent))
            else:
                return "未安装 prettier，跳过格式化"

        elif ext == ".go":
            if _has_tool("go"):
                return _run_cmd(["go", "fmt", str(p)], cwd=str(p.parent))
            else:
                return "未安装 go，跳过格式化"

        elif ext == ".rs":
            if _has_tool("cargo"):
                return _run_cmd(["cargo", "fmt"], cwd=str(p.parent))
            else:
                return "未安装 cargo，跳过格式化"

        return f"暂不支持 {ext} 文件的格式化"

    def run_tests(path: str = ".", pattern: str = "", verbose: bool = False) -> str:
        """运行测试套件"""
        cwd = Path(path).expanduser()
        if not cwd.exists():
            return f"[错误] 路径不存在: {path}"

        # 检测测试框架
        if (cwd / "pytest.ini").exists() or (cwd / "pyproject.toml").exists() or list(cwd.rglob("test_*.py")):
            if _has_tool("pytest"):
                cmd = ["pytest", str(cwd)]
                if pattern:
                    cmd.extend(["-k", pattern])
                if verbose:
                    cmd.append("-v")
                return _run_cmd(cmd, cwd=str(cwd), timeout=120)
            else:
                # 内置 unittest 发现
                return _run_cmd([sys.executable, "-m", "unittest", "discover", "-s", str(cwd)], cwd=str(cwd), timeout=120)

        elif (cwd / "package.json").exists():
            if _has_tool("npm"):
                return _run_cmd(["npm", "test"], cwd=str(cwd), timeout=120)
            else:
                return "未安装 npm"

        elif (cwd / "Cargo.toml").exists():
            if _has_tool("cargo"):
                return _run_cmd(["cargo", "test"], cwd=str(cwd), timeout=120)
            else:
                return "未安装 cargo"

        elif (cwd / "go.mod").exists():
            if _has_tool("go"):
                return _run_cmd(["go", "test", "./..."], cwd=str(cwd), timeout=120)
            else:
                return "未安装 go"

        return "未检测到支持的测试框架（pytest/unittest/npm/cargo/go）"

    def _has_tool(name: str) -> bool:
        """检查系统是否安装了某个命令"""
        from shutil import which
        return which(name) is not None

    def _run_cmd(cmd: list, cwd: str = "", timeout: int = 30) -> str:
        """运行外部命令"""
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            result = subprocess.run(
                cmd,
                cwd=cwd or None,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=creationflags,
            )
            output = result.stdout.strip()
            if result.stderr:
                output += f"\n[stderr] {result.stderr.strip()}"
            if result.returncode != 0:
                output += f"\n[exit code: {result.returncode}]"
            return output or "(无输出)"
        except subprocess.TimeoutExpired:
            return f"[错误] 命令超时 (> {timeout}s)"
        except Exception as e:
            return f"[错误] {e}"

    # 注册工具
    registry.register(
        "analyze_code", "分析代码结构（AST 提取函数/类/导入/复杂度）",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "代码文件路径"},
            },
            "required": ["path"],
        },
        analyze_code,
    )

    registry.register(
        "lint_code", "运行 Linter 检查代码问题",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "代码文件或目录路径"},
                "fix": {"type": "boolean", "description": "是否自动修复", "default": False},
            },
            "required": ["path"],
        },
        lint_code,
    )

    registry.register(
        "format_code", "格式化代码",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "代码文件路径"},
            },
            "required": ["path"],
        },
        format_code,
    )

    registry.register(
        "run_tests", "运行测试套件",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "项目根目录", "default": "."},
                "pattern": {"type": "string", "description": "测试匹配模式 (pytest -k)", "default": ""},
                "verbose": {"type": "boolean", "description": "详细输出", "default": False},
            },
            "required": [],
        },
        run_tests,
    )
