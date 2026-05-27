"""语义感知编辑计划器

根据修改影响面分析，生成多文件协调的编辑计划。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from .indexer import CodeIndexer
from .change_impact import ChangeImpactAnalyzer


@dataclass
class EditStep:
    """单个编辑步骤"""
    file_path: str
    description: str
    old_string: str = ""
    new_string: str = ""
    priority: int = 0  # 越小越优先执行


@dataclass
class EditPlan:
    """完整的编辑计划"""
    goal: str
    steps: list[EditStep] = field(default_factory=list)
    verification_steps: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [f"## Edit Plan: {self.goal}", ""]
        for i, step in enumerate(self.steps, 1):
            lines.append(f"{i}. [{step.priority}] `{step.file_path}`")
            lines.append(f"   {step.description}")
            if step.old_string:
                lines.append(f"   - SEARCH: `{step.old_string[:50]}...`")
            lines.append("")
        if self.verification_steps:
            lines.append("### Verification")
            for v in self.verification_steps:
                lines.append(f"- {v}")
        return "\n".join(lines)

    def sort_by_dependency(self):
        """按优先级排序（接口定义优先，调用方在后）"""
        self.steps.sort(key=lambda s: s.priority)


class SemanticEditPlanner:
    """
    语义感知编辑计划器。

    用法:
        planner = SemanticEditPlanner(indexer)
        plan = planner.plan_refactor("rename_function", "old_name", "new_name")
        for step in plan.steps:
            execute_edit(step.file_path, step.old_string, step.new_string)
    """

    def __init__(self, indexer: Optional[CodeIndexer] = None):
        self.indexer = indexer
        self.analyzer = ChangeImpactAnalyzer(indexer)

    def plan_refactor(self, refactor_type: str, target: str, new_value: str = "") -> EditPlan:
        """
        生成重构编辑计划。

        Args:
            refactor_type: "rename_function" | "rename_class" | "add_parameter" | "change_signature"
            target: 目标符号名
            new_value: 新值（新名、新签名等）
        """
        if self.indexer is None:
            return EditPlan(goal=f"{refactor_type}: {target} → {new_value}")

        report = self.analyzer.analyze(target)
        steps = []

        if refactor_type == "rename_function":
            steps = self._plan_rename(report, target, new_value)
        elif refactor_type == "rename_class":
            steps = self._plan_rename(report, target, new_value)
        elif refactor_type == "add_parameter":
            steps = self._plan_add_parameter(report, target, new_value)
        else:
            steps = self._plan_generic(report, target)

        # 排序：先改定义，再改调用
        for step in steps:
            if target in step.description and "definition" in step.description.lower():
                step.priority = 0
            elif "import" in step.description.lower():
                step.priority = 1
            else:
                step.priority = 2

        plan = EditPlan(
            goal=f"{refactor_type}: {target} → {new_value}",
            steps=steps,
            verification_steps=[
                f"Run tests in: {', '.join(report.test_files)}" if report.test_files else "Run project tests",
                "Check for import errors",
            ],
        )
        plan.sort_by_dependency()
        return plan

    def _plan_rename(self, report, old_name: str, new_name: str) -> list[EditStep]:
        steps = []
        # 1. 修改定义位置
        steps.append(EditStep(
            file_path=report.target_file,
            description=f"Rename definition: {old_name} → {new_name}",
            old_string=old_name,
            new_string=new_name,
            priority=0,
        ))
        # 2. 修改所有引用位置
        for sym in report.direct_callers + report.indirect_callers:
            steps.append(EditStep(
                file_path=sym.file_path,
                description=f"Update reference: {old_name} → {new_name}",
                old_string=old_name,
                new_string=new_name,
                priority=2,
            ))
        return steps

    def _plan_add_parameter(self, report, func_name: str, param_spec: str) -> list[EditStep]:
        """param_spec 如 'new_param: int = 0'"""
        steps = []
        # 1. 修改函数定义
        steps.append(EditStep(
            file_path=report.target_file,
            description=f"Add parameter to {func_name}",
            old_string=f"def {func_name}(",
            new_string=f"def {func_name}({param_spec}, ",
            priority=0,
        ))
        # 2. 修改所有调用点（添加默认值调用）
        for sym in report.direct_callers:
            steps.append(EditStep(
                file_path=sym.file_path,
                description=f"Update call to {func_name}",
                old_string=f"{func_name}(",
                new_string=f"{func_name}(...",  # 简化处理，实际需更精确
                priority=2,
            ))
        return steps

    def _plan_generic(self, report, target: str) -> list[EditStep]:
        steps = []
        for f in report.files_affected:
            steps.append(EditStep(
                file_path=f,
                description=f"Review and update references to {target}",
                priority=1,
            ))
        return steps
