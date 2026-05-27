"""
文档生成工具
===========
自动生成 API 文档、Changelog、README 草稿。
依赖: mkdocs, mkdocstrings-python（项目已有）
"""
from __future__ import annotations
import json
import subprocess
from pathlib import Path
from typing import Any


def generate_api_docs(project_root: str, output_dir: str = "docs/api", modules: list = None) -> dict:
    """
    基于代码 docstring 生成 API 文档（使用 mkdocstrings）。

    Args:
        project_root: 项目根目录
        output_dir: 文档输出目录
        modules: 要生成文档的模块列表，如 ["xagent.core", "xagent.tools"]

    Returns:
        {"ok": bool, "files": list[str], "error": str|None}
    """
    root = Path(project_root)
    out = root / output_dir
    out.mkdir(parents=True, exist_ok=True)

    modules = modules or []
    files = []

    # 为每个模块生成一个 markdown 文件
    for mod in modules:
        md_file = out / f"{mod.replace('.', '_')}.md"
        content = f"# `{mod}`\n\n::: {mod}\n"
        md_file.write_text(content, encoding="utf-8")
        files.append(str(md_file.relative_to(root)))

    return {
        "ok": True,
        "files": files,
        "error": None,
        "hint": "将这些文件加入 mkdocs.yml 的 nav 中以构建完整文档站点。",
    }


def generate_changelog(project_root: str, since_tag: str = None, output_file: str = "CHANGELOG.md") -> dict:
    """
    基于 git log 生成 changelog。

    Args:
        project_root: 项目根目录（需是 git 仓库）
        since_tag: 从某个 tag 开始生成
        output_file: 输出文件名

    Returns:
        {"ok": bool, "changelog": str, "output_path": str, "error": str|None}
    """
    root = Path(project_root)
    try:
        cmd = ["git", "log", "--pretty=format:%h %s (%an, %ad)", "--date=short"]
        if since_tag:
            cmd.append(f"{since_tag}..HEAD")
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return {"ok": False, "changelog": "", "output_path": "", "error": result.stderr}

        lines = result.stdout.strip().split("\n")
        sections = {"feat": [], "fix": [], "docs": [], "refactor": [], "test": [], "other": []}
        for line in lines:
            line_lower = line.lower()
            if line_lower.startswith("feat") or ": feat" in line_lower or "feat:" in line_lower:
                sections["feat"].append(line)
            elif line_lower.startswith("fix") or ": fix" in line_lower or "fix:" in line_lower:
                sections["fix"].append(line)
            elif line_lower.startswith("docs") or ": docs" in line_lower or "docs:" in line_lower:
                sections["docs"].append(line)
            elif line_lower.startswith("refactor") or ": refactor" in line_lower or "refactor:" in line_lower:
                sections["refactor"].append(line)
            elif line_lower.startswith("test") or ": test" in line_lower or "test:" in line_lower:
                sections["test"].append(line)
            else:
                sections["other"].append(line)

        changelog_lines = ["# Changelog\n", f"\n> Auto-generated from {len(lines)} commits.\n"]
        for section_name, section_title in [
            ("feat", "## Features"),
            ("fix", "## Bug Fixes"),
            ("docs", "## Documentation"),
            ("refactor", "## Refactoring"),
            ("test", "## Tests"),
            ("other", "## Other"),
        ]:
            if sections[section_name]:
                changelog_lines.append(f"\n{section_title}\n")
                for item in sections[section_name]:
                    changelog_lines.append(f"- {item}")

        changelog_text = "\n".join(changelog_lines)
        out_path = root / output_file
        out_path.write_text(changelog_text, encoding="utf-8")

        return {
            "ok": True,
            "changelog": changelog_text,
            "output_path": str(out_path),
            "error": None,
        }
    except Exception as e:
        return {"ok": False, "changelog": "", "output_path": "", "error": str(e)}


def register_docgen_tools(registry):
    registry.register(
        name="generate_api_docs",
        description="基于代码 docstring 生成 API 文档 markdown 文件（需配合 mkdocstrings 使用）。",
        parameters={
            "type": "object",
            "properties": {
                "project_root": {"type": "string", "description": "项目根目录"},
                "output_dir": {"type": "string", "description": "文档输出目录", "default": "docs/api"},
                "modules": {"type": "array", "description": "模块列表，如 [\"xagent.core\", \"xagent.tools\"]", "default": []},
            },
            "required": ["project_root"],
        },
        func=generate_api_docs,
        parallel_safe=True,
    )
    registry.register(
        name="generate_changelog",
        description="基于 git log 生成 CHANGELOG.md，按 feat/fix/docs/refactor/test 分类。",
        parameters={
            "type": "object",
            "properties": {
                "project_root": {"type": "string", "description": "项目根目录（git 仓库）"},
                "since_tag": {"type": "string", "description": "从某个 tag 开始生成", "default": None},
                "output_file": {"type": "string", "description": "输出文件名", "default": "CHANGELOG.md"},
            },
            "required": ["project_root"],
        },
        func=generate_changelog,
        parallel_safe=True,
    )
