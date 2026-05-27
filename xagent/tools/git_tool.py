"""
Git 操作工具
===========
编码 Agent 的必备能力。
"""
from __future__ import annotations
import subprocess
from pathlib import Path


def register_git_tools(registry):
    """注册 Git 相关工具"""

    def _run_git(args: list, cwd: str = "") -> str:
        work_dir = Path(cwd).expanduser() if cwd else Path.cwd()
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=10,
            )
            output = result.stdout.strip()
            if result.stderr and result.returncode != 0:
                output += f"\n[stderr] {result.stderr.strip()}"
            return output
        except FileNotFoundError:
            return "[错误] git 命令未找到"
        except Exception as e:
            return f"[错误] {e}"

    def git_status(cwd: str = "") -> str:
        return _run_git(["status", "--short"], cwd)

    def git_diff(cwd: str = "", staged: bool = False) -> str:
        args = ["diff", "--no-color"]
        if staged:
            args.append("--staged")
        return _run_git(args, cwd)

    def git_log(cwd: str = "", num: int = 10) -> str:
        return _run_git(["log", f"-{num}", "--oneline", "--no-decorate"], cwd)

    def git_branch(cwd: str = "") -> str:
        return _run_git(["branch", "-vv"], cwd)

    def git_show_file(path: str, ref: str = "HEAD") -> str:
        """查看文件在指定 commit 的内容"""
        return _run_git(["show", f"{ref}:{path}"])

    registry.register(
        "git_status", "查看 git 工作区状态",
        {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "工作目录", "default": ""},
            },
            "required": [],
        },
        git_status,
    )

    registry.register(
        "git_diff", "查看代码变更 diff",
        {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "工作目录", "default": ""},
                "staged": {"type": "boolean", "description": "是否查看已暂存的变更", "default": False},
            },
            "required": [],
        },
        git_diff,
    )

    registry.register(
        "git_log", "查看提交历史",
        {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "工作目录", "default": ""},
                "num": {"type": "integer", "description": "显示条数", "default": 10},
            },
            "required": [],
        },
        git_log,
    )

    registry.register(
        "git_branch", "查看分支信息",
        {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "工作目录", "default": ""},
            },
            "required": [],
        },
        git_branch,
    )
