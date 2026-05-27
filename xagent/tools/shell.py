"""
Shell 执行工具
============
半自动安全模型：危险命令需要用户确认。
Windows 兼容设计。
"""
from __future__ import annotations
import subprocess
import shlex
import os
import sys
from pathlib import Path


# 危险命令列表（可配置）
DEFAULT_DANGEROUS = [
    "rm", "rmdir", "del", "erase", "format", "mkfs",
    "dd", "shutdown", "reboot", "poweroff", "halt",
    "reg", "regedit", "rd", "format.com",
]


def register_shell_tools(registry):
    """注册 Shell 相关工具"""

    def run_command(command: str, cwd: str = "", timeout: int = None) -> str:
        """
        执行 Shell 命令，返回输出

        Args:
            command: 命令字符串
            cwd: 工作目录
            timeout: 超时秒数（默认读取自适应配置，低配置 120s，高配置无限制）
        """
        # 未传入 timeout 时读取自适应配置
        if timeout is None:
            try:
                from xagent.config import XAgentConfig
                cfg = XAgentConfig()
                adaptive_timeout = cfg._data.get("_adaptive", {}).get("shell_default_timeout")
                if adaptive_timeout is not None:
                    timeout = adaptive_timeout
            except Exception:
                pass

        work_dir = Path(cwd).expanduser() if cwd else Path.cwd()

        # Windows 下用 cmd /c 或 PowerShell
        if sys.platform == "win32":
            # 检测是否需要 PowerShell（管道、重定向等）
            if any(c in command for c in ["|", ">", "<", "`", "$"]):
                shell_cmd = ["powershell.exe", "-NoProfile", "-Command", command]
            else:
                shell_cmd = ["cmd", "/c", command]
            creationflags = subprocess.CREATE_NO_WINDOW
        else:
            shell_cmd = ["/bin/sh", "-c", command]
            creationflags = 0

        try:
            result = subprocess.run(
                shell_cmd,
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=creationflags,
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr] {result.stderr}"
            if result.returncode != 0:
                output += f"\n[exit code: {result.returncode}]"
            return output.strip()
        except subprocess.TimeoutExpired:
            return f"[错误] 命令超时 (> {timeout}s)"
        except Exception as e:
            return f"[错误] 执行失败: {e}"

    def is_dangerous(command: str, dangerous_list: list = None) -> bool:
        """判断命令是否包含危险操作"""
        dangerous = dangerous_list or DEFAULT_DANGEROUS
        # 简单匹配：命令开头或管道后的命令
        parts = command.replace("|", " ").replace("&&", " ").replace("||", " ").split()
        for part in parts:
            clean = part.strip().lower()
            # 去除路径前缀，如 /usr/bin/rm → rm
            base = os.path.basename(clean)
            if base in dangerous or clean in dangerous:
                return True
        return False

    registry.register(
        "run_command", "执行 Shell 命令（危险命令需要用户确认）",
        {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的命令"},
                "cwd": {"type": "string", "description": "工作目录", "default": ""},
                "timeout": {"type": "integer", "description": "超时秒数（默认无限制）", "default": None},
            },
            "required": ["command"],
        },
        run_command,
        dangerous=False,  # 危险判断在 Agent 层做
    )

    # 暴露危险检测函数供 Agent 层使用
    registry._is_dangerous = is_dangerous
    registry._dangerous_list = DEFAULT_DANGEROUS
