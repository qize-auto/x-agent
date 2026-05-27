"""Tests for persistence module."""
import tempfile
import time
from pathlib import Path

import pytest

from xagent.core.persistence.task_store import TaskStore, TaskSummary
from xagent.core.persistence.checkpoint import CheckpointManager
from xagent.core.task import TaskPlan, SubTask


class TestTaskStore:
    def test_save_and_load_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp)
            plan = TaskPlan(goal="Test goal")
            plan.subtasks = [
                SubTask(id="1", description="Step 1"),
                SubTask(id="2", description="Step 2", dependencies=["1"]),
            ]
            path = store.save_plan(plan)
            assert path.exists()

            loaded = store.load_plan(plan.id)
            assert loaded is not None
            assert loaded.goal == "Test goal"
            assert len(loaded.subtasks) == 2
            assert loaded.subtasks[0].id == "1"
            assert loaded.subtasks[1].dependencies == ["1"]

    def test_save_and_load_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp)
            state = {"messages": [{"role": "user", "content": "hi"}], "config": {"key": "value"}}
            path = store.save_state("task-123", state)
            assert path.exists()

            loaded = store.load_state("task-123")
            assert loaded == state

    def test_state_cleans_unserializable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp)
            state = {
                "messages": [{"role": "user", "content": "hi"}],
                "llm": object(),  # 不可序列化
                "config": {"key": "value"},
            }
            store.save_state("task-1", state)
            loaded = store.load_state("task-1")
            assert "messages" in loaded
            assert "llm" not in loaded
            assert "config" in loaded

    def test_list_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp)
            plan1 = TaskPlan(goal="Goal A")
            plan2 = TaskPlan(goal="Goal B")
            store.save_plan(plan1)
            store.save_plan(plan2)

            tasks = store.list_tasks()
            assert len(tasks) == 2
            goals = {t.goal for t in tasks}
            assert goals == {"Goal A", "Goal B"}

    def test_list_tasks_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp)
            plan1 = TaskPlan(goal="Goal A")
            plan1.status = "done"
            plan2 = TaskPlan(goal="Goal B")
            plan2.status = "executing"
            store.save_plan(plan1)
            store.save_plan(plan2)

            done_tasks = store.list_tasks(status_filter="done")
            assert len(done_tasks) == 1
            assert done_tasks[0].goal == "Goal A"

    def test_delete_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp)
            plan = TaskPlan(goal="To delete")
            store.save_plan(plan)
            assert store.task_exists(plan.id)

            assert store.delete_task(plan.id) is True
            assert store.task_exists(plan.id) is False
            assert store.delete_task(plan.id) is False

    def test_load_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp)
            assert store.load_plan("nonexistent") is None
            assert store.load_state("nonexistent") is None

    def test_meta_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp)
            plan = TaskPlan(goal="Meta test")
            store.save_plan(plan)
            meta_path = Path(tmp) / plan.id / "meta.json"
            assert meta_path.exists()
            import json
            meta = json.loads(meta_path.read_text())
            assert meta["goal"] == "Meta test"


class TestCheckpointManager:
    def test_checkpoint_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp)
            cp = CheckpointManager(store, auto_interval_sec=0)
            plan = TaskPlan(goal="Checkpoint test")
            path = cp.checkpoint("task-1", plan, {"msg": "hello"}, force=True)
            assert path is not None
            assert path.exists()

    def test_checkpoint_auto_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp)
            cp = CheckpointManager(store, auto_interval_sec=60)
            plan = TaskPlan(goal="Interval test")
            # 第一次强制保存
            cp.checkpoint("task-1", plan, force=True)
            # 第二次不强制，应被间隔阻止
            path2 = cp.checkpoint("task-1", plan, force=False)
            assert path2 is None

    def test_restore_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp)
            cp = CheckpointManager(store, auto_interval_sec=0)
            plan = TaskPlan(goal="Restore test")
            plan.status = "executing"
            cp.checkpoint("task-1", plan, {"x": 1}, force=True)

            loaded_plan, loaded_state = cp.restore_latest("task-1")
            assert loaded_plan is not None
            assert loaded_plan.goal == "Restore test"
            assert loaded_plan.status == "executing"
            assert loaded_state == {"x": 1}

    def test_restore_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp)
            cp = CheckpointManager(store)
            plan, state = cp.restore_latest("no-such-task")
            assert plan is None
            assert state is None

    def test_checkpoint_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp)
            cp = CheckpointManager(store, auto_interval_sec=0, max_checkpoints=3)
            for i in range(5):
                plan = TaskPlan(goal=f"Iteration {i}")
                cp.checkpoint("task-1", plan, force=True)
                time.sleep(0.01)

            checkpoints = cp.list_checkpoints("task-1")
            assert len(checkpoints) == 3

    def test_list_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp)
            cp = CheckpointManager(store, auto_interval_sec=0)
            plan = TaskPlan(goal="List test")
            cp.checkpoint("task-1", plan, force=True)

            cps = cp.list_checkpoints("task-1")
            assert len(cps) == 1
            assert cps[0]["plan_status"] == "planning"
