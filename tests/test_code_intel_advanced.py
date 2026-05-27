"""Tests for code intelligence advanced features (change impact + semantic edit)."""
import tempfile
from pathlib import Path

import pytest

from xagent.core.code_intel.indexer import CodeIndexer
from xagent.core.code_intel.change_impact import ChangeImpactAnalyzer
from xagent.core.code_intel.semantic_edit import SemanticEditPlanner, EditPlan


class TestChangeImpactAnalyzer:
    def test_analyze_simple_impact(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "core.py").write_text('''
def helper():
    """Called by process."""
    pass

def process():
    helper()
''')
            indexer = CodeIndexer(tmp)
            indexer.index_all()
            analyzer = ChangeImpactAnalyzer(indexer)
            report = analyzer.analyze("helper")

            assert report.target_symbol == "helper"
            assert report.target_file.endswith("core.py")
            assert len(report.files_affected) >= 1

    def test_analyze_unknown_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "mod.py").write_text("def known(): pass\n")
            indexer = CodeIndexer(tmp)
            indexer.index_all()
            analyzer = ChangeImpactAnalyzer(indexer)
            report = analyzer.analyze("nonexistent")
            assert report.target_symbol == "nonexistent"
            assert report.target_file == ""

    def test_identifies_test_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "src.py").write_text('''
def target():
    """Called by test_target."""
    pass
''')
            (Path(tmp) / "test_src.py").write_text("def test_target(): target()\n")
            indexer = CodeIndexer(tmp)
            indexer.index_all()
            analyzer = ChangeImpactAnalyzer(indexer)
            report = analyzer.analyze("target")
            # test_src.py 应被识别为测试文件
            assert any("test" in f.lower() for f in report.test_files)

    def test_report_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.py").write_text("def foo(): pass\n")
            indexer = CodeIndexer(tmp)
            indexer.index_all()
            analyzer = ChangeImpactAnalyzer(indexer)
            report = analyzer.analyze("foo")
            md = report.to_markdown()
            assert "Impact Analysis" in md
            assert "foo" in md

    def test_analyze_without_indexer(self):
        analyzer = ChangeImpactAnalyzer()
        report = analyzer.analyze("anything")
        assert report.target_file == ""


class TestSemanticEditPlanner:
    def test_plan_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "mod.py").write_text('''
def old_func():
    """Called by caller."""
    pass

def caller():
    old_func()
''')
            indexer = CodeIndexer(tmp)
            indexer.index_all()
            planner = SemanticEditPlanner(indexer)
            plan = planner.plan_refactor("rename_function", "old_func", "new_func")

            assert plan.goal == "rename_function: old_func → new_func"
            assert len(plan.steps) >= 1
            # 定义修改应优先
            def_step = [s for s in plan.steps if "definition" in s.description.lower()]
            assert len(def_step) >= 1
            assert def_step[0].priority == 0

    def test_plan_sorts_by_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "mod.py").write_text('''
def target():
    """Called by a and b."""
    pass

def a():
    target()

def b():
    target()
''')
            indexer = CodeIndexer(tmp)
            indexer.index_all()
            planner = SemanticEditPlanner(indexer)
            plan = planner.plan_refactor("rename_function", "target", "renamed")
            plan.sort_by_dependency()

            priorities = [s.priority for s in plan.steps]
            assert priorities == sorted(priorities)

    def test_plan_without_indexer(self):
        planner = SemanticEditPlanner()
        plan = planner.plan_refactor("rename_function", "foo", "bar")
        assert plan.goal == "rename_function: foo → bar"
        assert plan.steps == []

    def test_edit_plan_markdown(self):
        plan = EditPlan(
            goal="Test refactor",
            steps=[
                {"file_path": "a.py", "description": "Change A", "old_string": "old", "new_string": "new", "priority": 0},
            ],
            verification_steps=["Run tests"],
        )
        # 实际 EditPlan 的 to_markdown 需要 EditStep 对象
        from xagent.core.code_intel.semantic_edit import EditStep
        plan.steps = [EditStep(file_path="a.py", description="Change A", old_string="old", new_string="new", priority=0)]
        md = plan.to_markdown()
        assert "Test refactor" in md
        assert "a.py" in md
        assert "Run tests" in md
