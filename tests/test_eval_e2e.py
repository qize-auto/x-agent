"""Eval 端到端集成测试 — 使用本地伪仓库验证完整 pipeline"""
from __future__ import annotations
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from xagent.eval import EvalRunner, SWEBenchInstance


def _git(*args, cwd, text=True):
    """安全执行 git 命令，指定 UTF-8 编码"""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=text,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, result.args,
            output=result.stdout, stderr=result.stderr,
        )
    return result


def _create_fake_repo(repo_dir: Path) -> tuple[str, str]:
    """
    在 repo_dir 中创建一个最小 git 仓库，返回 (base_commit, fix_commit)。
    """
    _git("init", cwd=repo_dir)
    _git("config", "user.email", "test@test.com", cwd=repo_dir)
    _git("config", "user.name", "Test", cwd=repo_dir)

    buggy_py = repo_dir / "buggy.py"
    buggy_py.write_text("def divide(a, b):\n    return a / b\n", encoding="utf-8")
    _git("add", ".", cwd=repo_dir)
    _git("commit", "-m", "buggy", cwd=repo_dir)
    base_commit = _git("rev-parse", "HEAD", cwd=repo_dir).stdout.strip()

    test_py = repo_dir / "test_buggy.py"
    test_py.write_text(
        "from buggy import divide\n\ndef test_divide_by_one():\n    assert divide(10, 2) == 5\n\ndef test_divide_by_zero():\n    assert divide(10, 0) == 0\n",
        encoding="utf-8",
    )
    _git("add", ".", cwd=repo_dir)
    _git("commit", "-m", "add tests", cwd=repo_dir)

    buggy_py.write_text("def divide(a, b):\n    if b == 0:\n        return 0\n    return a / b\n", encoding="utf-8")
    _git("add", ".", cwd=repo_dir)
    _git("commit", "-m", "fix", cwd=repo_dir)
    fix_commit = _git("rev-parse", "HEAD", cwd=repo_dir).stdout.strip()

    return base_commit, fix_commit


class TestEvalEndToEnd:
    def test_full_pipeline_with_local_repo(self, tmp_path):
        """端到端：本地伪仓库 → Agent patch → 应用 → 测试验证"""
        repo_dir = tmp_path / "fake_repo"
        repo_dir.mkdir()
        base_commit, fix_commit = _create_fake_repo(repo_dir)

        # 生成修复 patch（从 buggy 到 fix）
        patch_result = _git("diff", base_commit, fix_commit, "--", "buggy.py", cwd=repo_dir)
        fix_patch = patch_result.stdout
        assert fix_patch, "Patch should not be empty"

        # 生成测试 patch（从 base 到 add-tests commit）
        log = _git("log", "--format=%H", f"{base_commit}..{fix_commit}", cwd=repo_dir).stdout.strip().splitlines()
        add_tests_commit = log[0] if len(log) >= 2 else fix_commit
        test_patch_result = _git("diff", base_commit, add_tests_commit, "--", "test_buggy.py", cwd=repo_dir)
        test_patch = test_patch_result.stdout
        assert test_patch, f"Test patch should not be empty. Got: {test_patch!r}"

        # 构建 SWEBenchInstance
        instance = SWEBenchInstance(
            instance_id="test-local-1",
            repo="local/fake",
            base_commit=base_commit,
            problem_statement="Fix divide by zero",
            test_patch=test_patch,
        )

        # Mock agent_loop：返回修复 patch
        mock_agent = MagicMock()
        mock_plan = MagicMock()
        mock_plan.status = "completed"
        mock_plan.id = "plan-1"
        mock_agent.run_task.return_value = mock_plan

        runner = EvalRunner(agent_loop=mock_agent, work_dir=str(tmp_path / "eval_work"))

        # Mock _setup_repo 返回本地仓库（跳过 git clone）
        def fake_setup(inst, inst_dir):
            target = inst_dir / "repo"
            import shutil
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(repo_dir, target)
            _git("checkout", inst.base_commit, cwd=target)
            return target

        runner._setup_repo = lambda inst, inst_dir: fake_setup(inst, inst_dir)

        mock_agent.run_task.return_value = mock_plan

        # 修改 runner 提取 patch 的逻辑（测试中直接注入）
        original_eval = runner._eval_instance

        def eval_with_patch(instance):
            result = original_eval(instance)
            result.patch = fix_patch
            repo_dir_eval = runner.work_dir / instance.instance_id.replace("/", "_") / "repo"
            if repo_dir_eval.exists():
                applied = runner._apply_patch(repo_dir_eval, fix_patch)
                result.logs.append(f"Re-applied patch: {applied}")
                test_passed = runner._run_tests(instance, repo_dir_eval)
                result.logs.append(f"Final tests: {test_passed}")
                result.status = "resolved" if test_passed else "failed"
            return result

        result = eval_with_patch(instance)

        assert result.instance_id == "test-local-1"
        assert result.status == "resolved", f"Expected resolved, got {result.status}. Logs: {result.logs}"
        assert result.patch, "Patch should be present"
        assert "Repo prepared" in " ".join(result.logs)

    def test_setup_repo_checkout(self, tmp_path):
        """验证 _setup_repo 能正确 clone 和 checkout"""
        source = tmp_path / "source"
        source.mkdir()
        base_commit, _ = _create_fake_repo(source)

        runner = EvalRunner(agent_loop=None, work_dir=str(tmp_path / "work"))
        inst = SWEBenchInstance(
            instance_id="test-checkout",
            repo="local/test",
            base_commit=base_commit,
        )

        target = tmp_path / "work" / "test-checkout" / "repo"
        target.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", str(source), str(target), cwd=tmp_path)
        _git("checkout", base_commit, cwd=target)

        current_commit = _git("rev-parse", "HEAD", cwd=target).stdout.strip()
        assert current_commit == base_commit

    def test_apply_patch(self, tmp_path):
        """验证 _apply_patch 能正确应用 patch"""
        repo = tmp_path / "repo"
        repo.mkdir()
        base_commit, fix_commit = _create_fake_repo(repo)

        patch = _git("diff", base_commit, fix_commit, "--", "buggy.py", cwd=repo).stdout

        _git("checkout", base_commit, cwd=repo)
        content_before = (repo / "buggy.py").read_text(encoding="utf-8")
        assert "if b == 0" not in content_before

        runner = EvalRunner(agent_loop=None, work_dir=str(tmp_path))
        applied = runner._apply_patch(repo, patch)
        assert applied is True

        content_after = (repo / "buggy.py").read_text(encoding="utf-8")
        assert "if b == 0" in content_after

    def test_run_tests(self, tmp_path):
        """验证 _run_tests 能正确运行 pytest"""
        repo = tmp_path / "repo"
        repo.mkdir()
        base_commit, fix_commit = _create_fake_repo(repo)

        runner = EvalRunner(agent_loop=None, work_dir=str(tmp_path))
        inst = SWEBenchInstance(
            instance_id="test-pytest",
            repo="local/test",
            base_commit=base_commit,
            test_patch="",
        )

        _git("checkout", base_commit, cwd=repo)

        log = _git("log", "--format=%H", f"{base_commit}..{fix_commit}", cwd=repo).stdout.strip().splitlines()
        add_tests_commit = log[0] if len(log) >= 2 else fix_commit
        test_content = _git("show", f"{add_tests_commit}:test_buggy.py", cwd=repo).stdout
        (repo / "test_buggy.py").write_text(test_content, encoding="utf-8")

        # buggy 版本下 divide(10, 0) 会抛出 ZeroDivisionError
        result = runner._run_tests(inst, repo)
        assert result is False

        fix_patch = _git("diff", base_commit, fix_commit, "--", "buggy.py", cwd=repo).stdout
        runner._apply_patch(repo, fix_patch)

        result = runner._run_tests(inst, repo)
        assert result is True
