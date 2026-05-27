"""Eval 框架测试"""
from __future__ import annotations
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from xagent.eval import (
    SWEBenchDataset, SWEBenchInstance,
    EvalRunner, EvalResult,
    ReportGenerator,
)


class TestSWEBenchDataset:
    def test_from_list(self):
        data = [
            {
                "instance_id": "django__django-1234",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            },
            {
                "instance_id": "scikit-learn__scikit-learn-5678",
                "repo": "scikit-learn/scikit-learn",
                "base_commit": "def456",
                "problem_statement": "Another bug",
            },
        ]
        ds = SWEBenchDataset.from_list(data)
        assert len(ds) == 2
        assert ds.instances[0].instance_id == "django__django-1234"
        assert ds.instances[0].repo_name == "django/django"

    def test_from_jsonl(self, tmp_path):
        f = tmp_path / "data.jsonl"
        with open(f, "w") as fh:
            fh.write(json.dumps({"instance_id": "a", "repo": "x/y", "base_commit": "1"}) + "\n")
            fh.write(json.dumps({"instance_id": "b", "repo": "x/z", "base_commit": "2"}) + "\n")
        ds = SWEBenchDataset.from_jsonl(f)
        assert len(ds) == 2
        ids = [i.instance_id for i in ds]
        assert "a" in ids
        assert "b" in ids

    def test_filter_by_repo(self):
        ds = SWEBenchDataset.from_list([
            {"instance_id": "a", "repo": "django/django"},
            {"instance_id": "b", "repo": "django/django"},
            {"instance_id": "c", "repo": "flask/flask"},
        ])
        filtered = ds.filter_by_repo("django/django")
        assert len(filtered) == 2

    def test_sample(self):
        ds = SWEBenchDataset.from_list([
            {"instance_id": f"i{i}", "repo": "x/y"} for i in range(100)
        ])
        sampled = ds.sample(5, seed=42)
        assert len(sampled) == 5


class TestEvalRunner:
    def test_run_without_agent_loop(self):
        ds = SWEBenchDataset.from_list([
            {
                "instance_id": "test-1",
                "repo": "owner/repo",
                "base_commit": "abc",
                "problem_statement": "Test bug",
            }
        ])
        runner = EvalRunner(agent_loop=None)
        # patch _setup_repo 避免实际 git 操作
        runner._setup_repo = MagicMock(return_value=Path("/tmp/fake"))
        runner._run_tests = MagicMock(return_value=False)

        results = runner.run(ds)
        assert len(results) == 1
        assert results[0].instance_id == "test-1"
        assert results[0].status == "failed"

    def test_run_with_mock_agent(self):
        ds = SWEBenchDataset.from_list([
            {
                "instance_id": "test-2",
                "repo": "owner/repo",
                "base_commit": "abc",
                "problem_statement": "Test bug",
            }
        ])
        mock_agent = MagicMock()
        mock_plan = MagicMock()
        mock_plan.status = "completed"
        mock_plan.id = "plan-1"
        mock_agent.run_task.return_value = mock_plan

        runner = EvalRunner(agent_loop=mock_agent)
        runner._setup_repo = MagicMock(return_value=Path("/tmp/fake"))
        runner._run_tests = MagicMock(return_value=True)

        results = runner.run(ds)
        assert results[0].status == "resolved"
        mock_agent.run_task.assert_called_once()

    def test_build_goal(self):
        runner = EvalRunner()
        inst = SWEBenchInstance(
            instance_id="i1", repo="a/b", base_commit="c1",
            problem_statement="Bug desc", hints_text="Hint 1",
        )
        goal = runner._build_goal(inst)
        assert "Bug desc" in goal
        assert "Hint 1" in goal


class TestReportGenerator:
    def test_summary(self):
        results = [
            EvalResult(instance_id="a", status="resolved", duration_sec=10),
            EvalResult(instance_id="b", status="failed", duration_sec=5),
            EvalResult(instance_id="c", status="error", duration_sec=3, error="timeout"),
        ]
        gen = ReportGenerator(results)
        stats = gen.summary()
        assert stats["total"] == 3
        assert stats["resolved"] == 1
        assert stats["failed"] == 1
        assert stats["errors"] == 1
        assert abs(stats["resolution_rate"] - 1 / 3) < 0.001

    def test_to_markdown(self):
        results = [
            EvalResult(instance_id="a", status="resolved", duration_sec=10),
        ]
        gen = ReportGenerator(results)
        md = gen.to_markdown()
        assert "SWE-bench 评估报告" in md
        assert "a" in md
        assert "resolved" in md

    def test_to_json(self, tmp_path):
        results = [
            EvalResult(instance_id="a", status="resolved", duration_sec=10),
        ]
        gen = ReportGenerator(results)
        out = tmp_path / "report.json"
        text = gen.to_json(str(out))
        assert "resolved" in text
        assert out.exists()
