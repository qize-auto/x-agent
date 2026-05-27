"""
Swarm 集成测试（通过 subprocess 验证多进程脚本）
================================================
spawn 模式下的多进程测试无法在 pytest 内直接运行
（pytest fixture/闭包与 spawn 不兼容）。
因此通过 subprocess 调用独立脚本，验证返回码和输出。
"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent


class TestSwarmPocScripts:
    """验证 tests/swarm_poc/ 下的多进程脚本"""

    def _run_script(self, script_name: str, timeout: int = 60) -> subprocess.CompletedProcess:
        path = PROJECT_ROOT / "tests" / "swarm_poc" / script_name
        env = {**dict(__import__("os").environ), "PYTHONIOENCODING": "utf-8"}
        return subprocess.run(
            [sys.executable, str(path)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

    def test_pickle_compat(self):
        """数据模型 pickle 兼容性"""
        result = self._run_script("pickle_compat_test.py")
        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    def test_controller_quick(self):
        """SwarmController 快速功能验证"""
        result = self._run_script("test_controller_quick.py", timeout=120)
        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    def test_shared_index(self):
        """共享内存代码索引"""
        result = self._run_script("test_shared_index.py", timeout=30)
        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    def test_benchmark_report(self):
        """性能基准测试生成报告"""
        result = self._run_script("benchmark.py", timeout=120)
        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
        report_path = PROJECT_ROOT / "tests" / "swarm_poc" / "benchmark_report.json"
        assert report_path.exists(), "基准测试报告未生成"
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert len(data) >= 3, "报告应包含至少 3 种模式"
        modes = {r["mode"] for r in data}
        assert "single_thread" in modes
        assert "swarm_2w" in modes
        assert "swarm_4w" in modes
