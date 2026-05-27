"""任务状态存储

将 TaskPlan 和 Agent 状态持久化到磁盘，支持跨进程恢复。
"""
from __future__ import annotations
import json
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..task import TaskPlan


@dataclass
class TaskSummary:
    """任务摘要（用于列表展示）"""
    task_id: str
    goal: str
    status: str
    created_at: float
    updated_at: float
    progress: str
    checkpoint_count: int = 0


class TaskStore:
    """
    任务状态存储管理器。

    存储结构:
        ~/.xagent/tasks/
          {task_id}/
            meta.json      # 元数据
            plan.json      # TaskPlan
            state.json     # AgentLoop 状态（messages, config）
            checkpoints/   # 检查点历史
    """

    def __init__(self, base_dir: str | Path = None):
        if base_dir is None:
            base_dir = Path.home() / ".xagent" / "tasks"
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ── 保存 ──

    def save_plan(self, plan: TaskPlan) -> Path:
        """保存任务计划"""
        task_dir = self._task_dir(plan.id)
        task_dir.mkdir(parents=True, exist_ok=True)

        plan_path = task_dir / "plan.json"
        plan_path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
                             encoding="utf-8")

        # 更新 meta
        meta = self._load_meta(plan.id)
        meta.update({
            "goal": plan.goal,
            "status": plan.status,
            "updated_at": time.time(),
            "progress": f"{plan.done_count()}/{plan.total_count()}",
        })
        self._save_meta(plan.id, meta)
        return plan_path

    def save_state(self, task_id: str, state: dict) -> Path:
        """保存 AgentLoop 状态（messages, config 等）"""
        task_dir = self._task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)

        state_path = task_dir / "state.json"
        # 过滤不可序列化的内容
        clean_state = self._clean_state(state)
        state_path.write_text(json.dumps(clean_state, ensure_ascii=False, indent=2),
                              encoding="utf-8")

        # 更新 meta
        meta = self._load_meta(task_id)
        meta["updated_at"] = time.time()
        self._save_meta(task_id, meta)
        return state_path

    # ── 恢复 ──

    def load_plan(self, task_id: str) -> Optional[TaskPlan]:
        """加载任务计划"""
        plan_path = self._task_dir(task_id) / "plan.json"
        if not plan_path.exists():
            return None
        try:
            data = json.loads(plan_path.read_text(encoding="utf-8"))
            return TaskPlan.from_dict(data)
        except Exception:
            return None

    def load_state(self, task_id: str) -> Optional[dict]:
        """加载 AgentLoop 状态"""
        state_path = self._task_dir(task_id) / "state.json"
        if not state_path.exists():
            return None
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    # ── 管理 ──

    def list_tasks(self, status_filter: str = None) -> list[TaskSummary]:
        """列出所有任务"""
        summaries = []
        for task_dir in self.base_dir.iterdir():
            if not task_dir.is_dir():
                continue
            meta = self._load_meta(task_dir.name)
            if status_filter and meta.get("status") != status_filter:
                continue
            checkpoints_dir = task_dir / "checkpoints"
            cp_count = len(list(checkpoints_dir.glob("*.json"))) if checkpoints_dir.exists() else 0
            summaries.append(TaskSummary(
                task_id=task_dir.name,
                goal=meta.get("goal", "Unknown"),
                status=meta.get("status", "unknown"),
                created_at=meta.get("created_at", 0.0),
                updated_at=meta.get("updated_at", 0.0),
                progress=meta.get("progress", "0/0"),
                checkpoint_count=cp_count,
            ))
        # 按更新时间倒序
        summaries.sort(key=lambda s: s.updated_at, reverse=True)
        return summaries

    def delete_task(self, task_id: str) -> bool:
        """删除任务及其所有数据"""
        task_dir = self._task_dir(task_id)
        if not task_dir.exists():
            return False
        shutil.rmtree(task_dir)
        return True

    def task_exists(self, task_id: str) -> bool:
        return self._task_dir(task_id).exists()

    # ── 内部 ──

    def _task_dir(self, task_id: str) -> Path:
        return self.base_dir / task_id

    def _load_meta(self, task_id: str) -> dict:
        meta_path = self._task_dir(task_id) / "meta.json"
        if meta_path.exists():
            try:
                return json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "task_id": task_id,
            "created_at": time.time(),
            "updated_at": time.time(),
            "status": "unknown",
            "goal": "",
        }

    def _save_meta(self, task_id: str, meta: dict):
        meta_path = self._task_dir(task_id) / "meta.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    @staticmethod
    def _clean_state(state: dict) -> dict:
        """清理不可序列化的状态内容"""
        clean = {}
        for key, value in state.items():
            if key in ("llm", "tools", "memory", "confirm_callback",
                       "status_callback", "ask_user_callback", "router",
                       "_cache_loop", "throttler", "intent_tracker"):
                # 跳过不可序列化的对象引用
                continue
            if key == "messages":
                # messages 是可序列化的
                clean[key] = value
            elif key in ("config", "contract"):
                # 尝试序列化
                try:
                    json.dumps(value)
                    clean[key] = value
                except (TypeError, ValueError):
                    pass
            else:
                try:
                    json.dumps(value)
                    clean[key] = value
                except (TypeError, ValueError):
                    pass
        return clean
