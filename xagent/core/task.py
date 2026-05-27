"""
Task 数据模型与状态机
====================
定义任务规划、子任务、执行状态的数据结构。
"""
from __future__ import annotations
import uuid
import time
from dataclasses import dataclass, field
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .requirement_contract import RequirementContract


@dataclass
class SubTask:
    """原子子任务"""
    id: str
    description: str
    tool_hint: str | None = None          # 建议工具，如 "read_file", "run_command"
    dependencies: list[str] = field(default_factory=list)  # 依赖的 subtask.id
    status: str = "pending"                # pending | running | verify | done | failed | skipped
    result: str = ""
    error: str = ""
    attempts: int = 0
    max_attempts: int = 3
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def is_blocked(self) -> bool:
        """是否还有未完成的依赖"""
        return False  # 由 TaskPlan 根据全局状态判断

    @property
    def duration(self) -> float:
        if self.started_at and self.finished_at:
            return self.finished_at - self.started_at
        return 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "tool_hint": self.tool_hint,
            "dependencies": self.dependencies,
            "status": self.status,
            "result": self.result[:200] if self.result else "",
            "error": self.error[:200] if self.error else "",
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration": round(self.duration, 2),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SubTask":
        st = cls(
            id=data["id"],
            description=data["description"],
            tool_hint=data.get("tool_hint"),
            dependencies=data.get("dependencies", []),
        )
        st.status = data.get("status", "pending")
        st.result = data.get("result", "")
        st.error = data.get("error", "")
        st.attempts = data.get("attempts", 0)
        st.max_attempts = data.get("max_attempts", 3)
        st.started_at = data.get("started_at", 0.0)
        st.finished_at = data.get("finished_at", 0.0)
        return st


@dataclass
class TaskPlan:
    """任务规划"""
    goal: str
    contract: "RequirementContract" = None  # 需求契约（可选）
    subtasks: list[SubTask] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    created_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    status: str = "planning"               # planning | executing | verifying | done | failed
    status_callback: Callable | None = None

    def find_subtask(self, sub_id: str) -> SubTask | None:
        """按 ID 查找子任务"""
        for st in self.subtasks:
            if st.id == sub_id:
                return st
        return None

    def get_ready_subtasks(self) -> list[SubTask]:
        """获取所有依赖已满足且状态为 pending 的子任务"""
        done_ids = {st.id for st in self.subtasks if st.status in ("done", "skipped")}
        ready = []
        for st in self.subtasks:
            if st.status == "pending" and all(dep in done_ids for dep in st.dependencies):
                ready.append(st)
        return ready

    def all_done(self) -> bool:
        """所有子任务是否已完成"""
        return all(st.status in ("done", "skipped") for st in self.subtasks)

    def any_failed(self) -> bool:
        """是否有子任务失败"""
        return any(st.status == "failed" for st in self.subtasks)

    def done_count(self) -> int:
        """已完成子任务数"""
        return sum(1 for st in self.subtasks if st.status in ("done", "skipped"))

    def total_count(self) -> int:
        """子任务总数"""
        return len(self.subtasks)

    def update_status(self, new_status: str):
        """更新计划状态并触发回调"""
        self.status = new_status
        if self.status_callback:
            self.status_callback(new_status, self.summary())

    def summary(self) -> dict:
        """生成计划摘要"""
        return {
            "plan_id": self.id,
            "goal": self.goal,
            "status": self.status,
            "progress": f"{self.done_count()}/{self.total_count()}",
            "subtasks": [st.to_dict() for st in self.subtasks],
        }

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "id": self.id,
            "goal": self.goal,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "subtasks": [st.to_dict() for st in self.subtasks],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskPlan":
        """从字典反序列化"""
        plan = cls(
            goal=data["goal"],
            id=data.get("id", "unknown"),
        )
        plan.status = data.get("status", "planning")
        plan.created_at = data.get("created_at", 0.0)
        plan.finished_at = data.get("finished_at", 0.0)
        plan.subtasks = [SubTask.from_dict(st) for st in data.get("subtasks", [])]
        return plan

    def to_markdown(self) -> str:
        """生成人类可读的任务列表"""
        lines = [f"## 任务: {self.goal}", ""]
        for st in self.subtasks:
            icon = {
                "pending": "⬜", "running": "🔄", "verify": "🔍",
                "done": "✅", "failed": "❌", "skipped": "⏭️",
            }.get(st.status, "⬜")
            dep = f" (依赖: {', '.join(st.dependencies)})" if st.dependencies else ""
            lines.append(f"{icon} [{st.id}] {st.description}{dep}")
        return "\n".join(lines)
