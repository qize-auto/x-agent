"""检查点管理器

在任务执行过程中自动保存检查点，支持崩溃恢复。
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Optional

from .task_store import TaskStore
from ..task import TaskPlan


class CheckpointManager:
    """
    自动检查点管理器。

    用法:
        cp = CheckpointManager(task_store)
        cp.checkpoint(task_id, plan, messages)
        # 恢复时
        plan, messages = cp.restore_latest(task_id)
    """

    def __init__(self, store: TaskStore = None,
                 auto_interval_sec: float = 300,  # 5 分钟
                 max_checkpoints: int = 10):
        self.store = store or TaskStore()
        self.auto_interval_sec = auto_interval_sec
        self.max_checkpoints = max_checkpoints
        self._last_checkpoint: dict[str, float] = {}

    def checkpoint(self, task_id: str, plan: TaskPlan,
                   state: dict = None, force: bool = False) -> Path | None:
        """
        保存检查点。

        Args:
            force: True 时无视时间间隔强制保存
        """
        now = time.time()
        last = self._last_checkpoint.get(task_id, 0)
        if not force and (now - last) < self.auto_interval_sec:
            return None

        cp_dir = self.store._task_dir(task_id) / "checkpoints"
        cp_dir.mkdir(parents=True, exist_ok=True)

        # 保存新检查点（文件名包含毫秒以确保唯一性）
        cp_name = f"cp_{int(now * 1000)}.json"
        cp_path = cp_dir / cp_name
        checkpoint_data = {
            "timestamp": now,
            "plan": plan.to_dict(),
            "state": state or {},
        }
        cp_path.write_text(json.dumps(checkpoint_data, ensure_ascii=False, indent=2),
                           encoding="utf-8")

        # 清理旧检查点（保存后再旋转，确保总数不超过限制）
        self._rotate_checkpoints(cp_dir)

        self._last_checkpoint[task_id] = now
        return cp_path

    def restore_latest(self, task_id: str) -> tuple[Optional[TaskPlan], Optional[dict]]:
        """恢复到最新的检查点"""
        cp_dir = self.store._task_dir(task_id) / "checkpoints"
        if not cp_dir.exists():
            return None, None

        checkpoints = sorted(cp_dir.glob("cp_*.json"), key=lambda p: p.name, reverse=True)
        if not checkpoints:
            return None, None

        latest = checkpoints[0]
        try:
            data = json.loads(latest.read_text(encoding="utf-8"))
            plan = TaskPlan.from_dict(data["plan"])
            state = data.get("state", {})
            return plan, state
        except Exception:
            return None, None

    def list_checkpoints(self, task_id: str) -> list[dict]:
        """列出所有检查点"""
        cp_dir = self.store._task_dir(task_id) / "checkpoints"
        if not cp_dir.exists():
            return []

        checkpoints = []
        for cp_path in sorted(cp_dir.glob("cp_*.json"), key=lambda p: p.name, reverse=True):
            try:
                data = json.loads(cp_path.read_text(encoding="utf-8"))
                checkpoints.append({
                    "file": cp_path.name,
                    "timestamp": data.get("timestamp", 0),
                    "plan_status": data.get("plan", {}).get("status", "unknown"),
                })
            except Exception:
                pass
        return checkpoints

    def _rotate_checkpoints(self, cp_dir: Path):
        """保留最新的 N 个检查点（按文件名中的时间戳排序）"""
        checkpoints = sorted(cp_dir.glob("cp_*.json"), key=lambda p: p.name)
        while len(checkpoints) > self.max_checkpoints:
            checkpoints.pop(0).unlink()
