"""评估运行器 — 执行 SWE-bench 评估流水线"""
from __future__ import annotations
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .swe_bench import SWEBenchDataset, SWEBenchInstance


@dataclass
class EvalResult:
    """单个实例的评估结果"""
    instance_id: str
    status: str = ""          # resolved | failed | error | skipped
    patch: str = ""           # Agent 生成的 patch
    error: str = ""           # 错误信息
    duration_sec: float = 0.0
    logs: list[str] = field(default_factory=list)


class EvalRunner:
    """
    SWE-bench 评估运行器。

    用法:
        runner = EvalRunner(agent_loop=agent_loop, work_dir="/tmp/eval")
        dataset = SWEBenchDataset.from_jsonl("swe-bench-lite.jsonl")
        results = runner.run(dataset)
        report = runner.generate_report(results)
    """

    def __init__(self, agent_loop=None, work_dir: str = "", max_workers: int = 1):
        self.agent_loop = agent_loop
        self.work_dir = Path(work_dir) if work_dir else Path(tempfile.gettempdir()) / "xagent_eval"
        self.max_workers = max_workers
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def run(self, dataset: SWEBenchDataset,
            instance_filter: Callable = None,
            progress_callback: Callable = None) -> list[EvalResult]:
        """
        运行评估流水线。

        Args:
            dataset: SWE-bench 数据集
            instance_filter: 可选的过滤函数 f(instance) -> bool
            progress_callback: 可选的进度回调 f(current, total, instance_id)
        """
        results = []
        instances = dataset.instances
        if instance_filter:
            instances = [i for i in instances if instance_filter(i)]

        for idx, instance in enumerate(instances, 1):
            if progress_callback:
                progress_callback(idx, len(instances), instance.instance_id)

            result = self._eval_instance(instance)
            results.append(result)

        return results

    def _eval_instance(self, instance: SWEBenchInstance) -> EvalResult:
        """评估单个实例"""
        import time
        start = time.time()
        result = EvalResult(instance_id=instance.instance_id)

        instance_dir = self.work_dir / instance.instance_id.replace("/", "_")
        instance_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 1. 准备环境
            repo_dir = self._setup_repo(instance, instance_dir)
            result.logs.append(f"Repo prepared at {repo_dir}")

            # 2. 运行 Agent 修复
            if self.agent_loop:
                goal = self._build_goal(instance)
                plan = self.agent_loop.run_task(goal, mode="interactive")
                result.logs.append(f"Agent plan status: {plan.status}")
                # 从 git diff 提取 Agent 的修改
                try:
                    diff_result = subprocess.run(
                        ["git", "-C", str(repo_dir), "diff"],
                        capture_output=True, text=True, check=True,
                    )
                    result.patch = diff_result.stdout
                except Exception:
                    result.patch = ""
            else:
                result.logs.append("No agent_loop provided, skipping fix generation")
                result.patch = ""

            # 3. 应用 patch（如果有）
            if result.patch:
                applied = self._apply_patch(repo_dir, result.patch)
                result.logs.append(f"Patch applied: {applied}")

            # 4. 运行测试验证
            test_passed = self._run_tests(instance, repo_dir)
            result.logs.append(f"Tests passed: {test_passed}")

            # 5. 判定结果
            if test_passed:
                result.status = "resolved"
            else:
                result.status = "failed"

        except Exception as e:
            result.status = "error"
            result.error = str(e)
            result.logs.append(f"Error: {e}")

        result.duration_sec = time.time() - start
        return result

    def _setup_repo(self, instance: SWEBenchInstance, instance_dir: Path) -> Path:
        """克隆仓库并 checkout 到 buggy commit"""
        repo_dir = instance_dir / "repo"
        repo_url = f"https://github.com/{instance.repo}.git"

        if not (repo_dir / ".git").exists():
            # 克隆
            subprocess.run(
                ["git", "clone", repo_url, str(repo_dir)],
                check=True, capture_output=True, text=True,
            )

        # checkout 到 base commit
        subprocess.run(
            ["git", "-C", str(repo_dir), "checkout", instance.base_commit],
            check=True, capture_output=True, text=True,
        )

        return repo_dir

    def _build_goal(self, instance: SWEBenchInstance) -> str:
        """构建给 Agent 的目标"""
        goal = f"Fix the following issue in {instance.repo}:\n\n{instance.problem_statement}"
        if instance.hints_text:
            goal += f"\n\nHints:\n{instance.hints_text}"
        return goal

    def _apply_patch(self, repo_dir: Path, patch: str) -> bool:
        """在仓库中应用 patch"""
        try:
            patch_file = repo_dir / "agent.patch"
            patch_file.write_text(patch, encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repo_dir), "apply", str(patch_file)],
                check=True, capture_output=True, text=True,
            )
            return True
        except Exception:
            return False

    def _run_tests(self, instance: SWEBenchInstance, repo_dir: Path) -> bool:
        """运行测试验证修复"""
        try:
            # 如有测试 patch，先应用（获取测试文件）
            if instance.test_patch:
                self._apply_patch(repo_dir, instance.test_patch)

            # 运行测试（简化：尝试运行 pytest 或 setup.py test）
            # 实际 SWE-bench 使用更复杂的测试发现逻辑
            result = subprocess.run(
                ["python", "-m", "pytest", "-x", "-q"],
                cwd=str(repo_dir),
                capture_output=True, text=True, timeout=300,
            )
            return result.returncode == 0
        except Exception:
            return False
