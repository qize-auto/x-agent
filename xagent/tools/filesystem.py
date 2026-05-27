"""
文件系统工具
===========
编码 Agent 的基础能力：读、写、搜索文件。
"""
from __future__ import annotations
import os
import fnmatch
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional


def _load_xagentignore(project_root: str = ".") -> list[str]:
    """加载 .xagentignore 文件，返回忽略模式列表"""
    default_patterns = [
        ".git/", ".git/*",
        "__pycache__/", "__pycache__/*",
        "*.pyc", "*.pyo",
        "node_modules/", "node_modules/*",
        ".env", ".env.*",
        "*.key", "*.pem", "*.crt",
        ".ssh/", ".ssh/*",
        ".xagent/config.json",
    ]
    ignore_file = Path(project_root) / ".xagentignore"
    if ignore_file.exists():
        try:
            custom = [line.strip() for line in ignore_file.read_text(encoding="utf-8").splitlines()
                      if line.strip() and not line.strip().startswith("#")]
            default_patterns.extend(custom)
        except Exception:
            pass
    return default_patterns


def _is_ignored(path: str, patterns: list[str]) -> bool:
    """检查路径是否匹配忽略模式"""
    p = Path(path).resolve()
    p_str = str(p).replace("\\", "/")
    for pattern in patterns:
        pat = pattern.replace("\\", "/")
        # 精确匹配或通配符匹配
        if fnmatch.fnmatch(p_str, pat) or fnmatch.fnmatch(p.name, pat):
            return True
        # 目录前缀匹配
        if pat.endswith("/") and p_str.startswith(pat.rstrip("/") + "/"):
            return True
    return False


def register_filesystem_tools(registry, project_root: str = "."):
    """注册文件系统相关工具"""
    ignore_patterns = _load_xagentignore(project_root)

    def read_file(path: str, offset: int = 0, limit: int = 200) -> str:
        """读取文件内容，支持行范围"""
        p = Path(path).expanduser()
        if _is_ignored(str(p), ignore_patterns):
            return f"[拒绝] 路径在 .xagentignore 保护列表中: {path}"
        if not p.exists():
            return f"[错误] 文件不存在: {path}"
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
            if limit == 0:
                return "\n".join(lines[offset:])
            return "\n".join(lines[offset:offset + limit])
        except Exception as e:
            return f"[错误] 读取失败: {e}"

    def write_file(path: str, content: str, append: bool = False) -> str:
        """写入文件内容"""
        p = Path(path).expanduser()
        if _is_ignored(str(p), ignore_patterns):
            return f"[拒绝] 路径在 .xagentignore 保护列表中: {path}"
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with open(p, mode, encoding="utf-8") as f:
                f.write(content)
            return f"已{'追加' if append else '写入'}: {path} ({len(content)} 字符)"
        except Exception as e:
            return f"[错误] 写入失败: {e}"

    def edit_file(path: str, old_string: str, new_string: str) -> str:
        """
        SEARCH/REPLACE 编辑文件（支持单块和多块 Aider 格式）

        特性：
        - 原子化：所有块在内存中验证通过后才写入磁盘
        - 失败回滚：任何一块失败时整篇文件保持不变
        - 模糊匹配：尝试忽略前后空白差异
        - Git 备份：如果在 git 仓库中，编辑前自动 stash

        多块格式示例：
        <<<<<<< SEARCH
        old content 1
        =======
        new content 1
        >>>>>>> REPLACE

        <<<<<<< SEARCH
        old content 2
        =======
        new content 2
        >>>>>>> REPLACE
        """
        p = Path(path).expanduser()
        if _is_ignored(str(p), ignore_patterns):
            return f"[拒绝] 路径在 .xagentignore 保护列表中: {path}"
        if not p.exists():
            return f"[错误] 文件不存在: {path}"
        try:
            original_content = p.read_text(encoding="utf-8")
            content = original_content

            # 检测是否为 Aider 多块格式
            if "<<<<<<< SEARCH" in old_string:
                blocks = _parse_edit_blocks(old_string)
                if isinstance(blocks, str):  # 错误信息
                    return f"[错误] {blocks}"
                applied = 0
                for search, replace in blocks:
                    if search not in content:
                        # 尝试模糊匹配（忽略前后空白）
                        fuzzy_search = search.strip()
                        fuzzy_content = content.strip()
                        if fuzzy_search in fuzzy_content:
                            # 找到匹配，但空白不同，需要精确替换
                            # 尝试在原始内容中查找包含该文本的行范围
                            idx = content.find(fuzzy_search)
                            if idx != -1:
                                content = content.replace(content[idx:idx+len(fuzzy_search)], replace, 1)
                                applied += 1
                                continue
                        return f"[错误] 未找到匹配块 #{applied + 1} (已应用 {applied} 块，文件未修改)"
                    content = content.replace(search, replace, 1)
                    applied += 1
            else:
                # 单块替换（向后兼容）
                if old_string not in content:
                    return f"[错误] 未找到匹配字符串"
                content = content.replace(old_string, new_string, 1)

            # 原子写入：先验证语法，再验证 lint，最后写入磁盘
            syntax_err = _check_syntax(str(p), content)
            if syntax_err:
                return f"[拒绝] 语法检查失败，文件未修改: {syntax_err}"

            lint_err = _auto_verify(str(p), content)
            if lint_err:
                return f"[拒绝] 代码验证失败，文件未修改:\n{lint_err}"

            # Git 备份（如果可用）
            _git_backup(str(p))

            p.write_text(content, encoding="utf-8")
            return f"已编辑: {path}"
        except Exception as e:
            return f"[错误] 编辑失败: {e}"

    def _parse_edit_blocks(text: str):
        """解析 Aider 风格的多块 SEARCH/REPLACE"""
        blocks = []
        remaining = text
        while True:
            start = remaining.find("<<<<<<< SEARCH")
            if start == -1:
                break
            end_search = remaining.find("=======", start)
            end_replace = remaining.find(">>>>>>> REPLACE", end_search)
            if end_search == -1 or end_replace == -1:
                return "多块格式解析失败: 缺少 ======= 或 >>>>>>> REPLACE"
            search = remaining[start + len("<<<<<<< SEARCH\n"):end_search]
            # 去除尾部换行（Aider 格式惯例）
            if search.endswith("\n"):
                search = search[:-1]
            replace = remaining[end_search + len("=======\n"):end_replace]
            if replace.endswith("\n"):
                replace = replace[:-1]
            blocks.append((search, replace))
            remaining = remaining[end_replace + len(">>>>>>> REPLACE"):]
        if not blocks:
            return "未找到有效的 SEARCH/REPLACE 块"
        return blocks

    def _check_syntax(path: str, content: str) -> str:
        """语法验证：Python 用内置 compile，其他语言用 tree-sitter（可选）"""
        ext = Path(path).suffix.lower()
        # Python: 始终使用内置 compile（最可靠）
        if ext == ".py":
            try:
                compile(content, path, "exec")
            except SyntaxError as e:
                return f"语法错误 行{e.lineno}: {e.msg}"
            return ""
        # 其他语言：尝试 tree-sitter（可选依赖）
        try:
            import tree_sitter
            from tree_sitter import Parser
            parser = Parser()
            # tree-sitter 需要预编译语言库，这里仅做占位
            return ""
        except ImportError:
            return ""  # tree-sitter 未安装，跳过其他语言检查
        except Exception:
            return ""

    def _auto_verify(path: str, content: str) -> str:
        """
        自动代码验证：语法通过后执行轻量 lint。
        只检查单个文件，不扫描目录，防止卡死 CPU。
        根据硬件档位动态调整行为（低配置超时更短但不牺牲语法检查）。
        返回空字符串表示通过，否则返回错误信息。
        """
        # 检查自适应配置是否启用 lint
        try:
            from xagent.config import XAgentConfig
            cfg = XAgentConfig()
            if not cfg._data.get("_adaptive", {}).get("enable_lint", True):
                return ""  # lint 被禁用，跳过（语法检查已在外部执行）
        except Exception:
            pass

        ext = Path(path).suffix.lower()
        if ext == ".py":
            return _lint_single_file(content)
        # 其他语言暂不做自动 lint（避免引入复杂依赖）
        return ""

    def _lint_single_file(content: str) -> str:
        """
        对单个 Python 文件内容做 lint。
        超时根据硬件档位动态调整（高配置 120s，中配置 60s，低配置 30s）。
        工具不可用时优雅跳过。超时后不阻断（语法检查已通过，精准度底线保住）。
        """
        # 读取自适应 lint 超时
        lint_timeout = 60
        try:
            from xagent.config import XAgentConfig
            cfg = XAgentConfig()
            lint_timeout = cfg._data.get("_adaptive", {}).get("lint_timeout_sec", 60)
        except Exception:
            pass

        # 写入临时文件
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
                f.write(content)
                tmp = f.name
        except Exception:
            return ""  # 临时文件失败则跳过

        errors = []
        try:
            # 优先 ruff（最快，支持 pyproject.toml）
            if shutil.which("ruff"):
                result = subprocess.run(
                    ["ruff", "check", tmp],
                    capture_output=True, text=True, timeout=lint_timeout,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                if result.stdout.strip():
                    errors.append(result.stdout.strip())
            # 其次 flake8
            elif shutil.which("flake8"):
                result = subprocess.run(
                    ["flake8", tmp],
                    capture_output=True, text=True, timeout=lint_timeout,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                if result.stdout.strip():
                    errors.append(result.stdout.strip())
            # 兜底 py_compile
            else:
                try:
                    py_compile.compile(tmp, doraise=True)
                except py_compile.PyCompileError as e:
                    errors.append(str(e))
        except subprocess.TimeoutExpired:
            # 超时 ≠ 跳过验证，超时 = 状态未知
            return "[lint 检查超时] 文件语法正确，但静态分析未完成，状态未知"
        finally:
            Path(tmp).unlink(missing_ok=True)

        if errors:
            return "\n".join(errors[:5])  # 最多显示 5 条，避免信息过载
        return ""

    def _git_backup(path: str):
        """如果在 git 仓库中，编辑前自动 stash 备份"""
        import subprocess
        try:
            result = subprocess.run(
                ["git", "ls-files", "--error-unmatch", path],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if result.returncode == 0:
                subprocess.run(
                    ["git", "stash", "push", "-m", f"xagent-backup-{Path(path).name}", "--", path],
                    capture_output=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
        except Exception:
            pass  # 非 git 仓库或 git 不可用，静默跳过

    def list_directory(path: str = ".") -> str:
        """列出目录内容"""
        p = Path(path).expanduser()
        if _is_ignored(str(p), ignore_patterns):
            return f"[拒绝] 路径在 .xagentignore 保护列表中: {path}"
        if not p.exists():
            return f"[错误] 目录不存在: {path}"
        try:
            items = []
            for item in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                prefix = "[D]" if item.is_dir() else "[F]"
                items.append(f"{prefix} {item.name}")
            return "\n".join(items)
        except Exception as e:
            return f"[错误] {e}"

    def search_files(path: str = ".", pattern: str = "", glob: str = "*") -> str:
        """在目录中搜索文件或内容"""
        p = Path(path).expanduser()
        if _is_ignored(str(p), ignore_patterns):
            return f"[拒绝] 路径在 .xagentignore 保护列表中: {path}"
        results = []
        try:
            for f in p.rglob(glob):
                if f.is_file():
                    if pattern:
                        try:
                            content = f.read_text(encoding="utf-8", errors="ignore")
                            if pattern in content:
                                lines = [i + 1 for i, line in enumerate(content.splitlines()) if pattern in line]
                                results.append(f"{f}: 行 {lines[:5]}{'...' if len(lines) > 5 else ''}")
                        except Exception:
                            pass
                    else:
                        results.append(str(f))
                if len(results) >= 50:
                    results.append("... (结果过多，已截断)")
                    break
            return "\n".join(results) if results else "未找到匹配"
        except Exception as e:
            return f"[错误] {e}"

    def get_file_info(path: str) -> str:
        """获取文件元信息"""
        p = Path(path).expanduser()
        if _is_ignored(str(p), ignore_patterns):
            return f"[拒绝] 路径在 .xagentignore 保护列表中: {path}"
        if not p.exists():
            return f"[错误] 不存在: {path}"
        stat = p.stat()
        return (
            f"路径: {p}\n"
            f"类型: {'目录' if p.is_dir() else '文件'}\n"
            f"大小: {stat.st_size} 字节\n"
            f"修改时间: {__import__('datetime').datetime.fromtimestamp(stat.st_mtime)}"
        )

    registry.register(
        "read_file", "读取文件内容，支持指定起始行和行数",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "offset": {"type": "integer", "description": "起始行（0-based）", "default": 0},
                "limit": {"type": "integer", "description": "读取行数，0=全部", "default": 200},
            },
            "required": ["path"],
        },
        read_file,
    )

    registry.register(
        "write_file", "写入或覆盖文件",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "文件内容"},
                "append": {"type": "boolean", "description": "是否追加", "default": False},
            },
            "required": ["path", "content"],
        },
        write_file,
    )

    registry.register(
        "edit_file", "SEARCH/REPLACE 编辑文件（支持单块和多块 Aider 格式）",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "old_string": {"type": "string", "description": "要替换的旧字符串。支持单块精确匹配，或 Aider 多块格式 <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE"},
                "new_string": {"type": "string", "description": "新字符串。当使用 Aider 多块格式时，此参数可留空（替换内容包含在 old_string 中）"},
            },
            "required": ["path", "old_string"],
        },
        edit_file,
    )

    registry.register(
        "list_directory", "列出目录内容",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径", "default": "."},
            },
            "required": [],
        },
        list_directory,
    )

    registry.register(
        "search_files", "搜索文件或文件内容",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "搜索根目录", "default": "."},
                "pattern": {"type": "string", "description": "内容匹配字符串（为空只列出文件）", "default": ""},
                "glob": {"type": "string", "description": "文件通配符", "default": "*"},
            },
            "required": [],
        },
        search_files,
    )

    registry.register(
        "get_file_info", "获取文件元信息",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
            },
            "required": ["path"],
        },
        get_file_info,
    )
