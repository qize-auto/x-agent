"""Swarm 模块单元测试（非多进程部分）"""
from __future__ import annotations
import json
import time
import tempfile
from pathlib import Path

import pytest

from xagent.core.swarm.checkpoint import CheckpointStore, SwarmCheckpoint
from xagent.core.swarm.controller import AgentHeartbeater, CircuitBreaker
from xagent.core.swarm.executor import SwarmExecutor


class TestCheckpointStore:
    def test_save_and_load(self, tmp_path):
        store = CheckpointStore(tmp_path)
        cp = SwarmCheckpoint(
            checkpoint_id="cp_1",
            task_id="t1",
            status="completed",
            created_at=time.time(),
            updated_at=time.time(),
            node_id="n1",
            result={"output": "hello"},
        )
        store.save(cp)
        loaded = store.load("cp_1")
        assert loaded is not None
        assert loaded.status == "completed"
        assert loaded.result["output"] == "hello"

    def test_load_missing(self, tmp_path):
        store = CheckpointStore(tmp_path)
        assert store.load("noexist") is None

    def test_list_all(self, tmp_path):
        store = CheckpointStore(tmp_path)
        for i in range(3):
            store.save(SwarmCheckpoint(
                checkpoint_id=f"cp_{i}",
                task_id=f"t{i}",
                status="completed",
                created_at=time.time(),
                updated_at=time.time(),
            ))
        cps = store.list_all()
        assert len(cps) == 3

    def test_delete(self, tmp_path):
        store = CheckpointStore(tmp_path)
        store.save(SwarmCheckpoint(
            checkpoint_id="del_me",
            task_id="t",
            status="completed",
            created_at=time.time(),
            updated_at=time.time(),
        ))
        assert store.delete("del_me") is True
        assert store.load("del_me") is None
        assert store.delete("noexist") is False

    def test_cleanup_old(self, tmp_path):
        store = CheckpointStore(tmp_path)
        # 旧的
        store.save(SwarmCheckpoint(
            checkpoint_id="old",
            task_id="t",
            status="completed",
            created_at=time.time() - 100000,
            updated_at=time.time() - 100000,
        ))
        # 新的
        store.save(SwarmCheckpoint(
            checkpoint_id="new",
            task_id="t",
            status="completed",
            created_at=time.time(),
            updated_at=time.time(),
        ))
        removed = store.cleanup_old(max_age_sec=3600)
        assert removed == 1
        assert store.load("old") is None
        assert store.load("new") is not None


class TestAgentHeartbeater:
    def test_register_and_heartbeat(self):
        hb = AgentHeartbeater()
        hb.register("a1")
        hb.heartbeat("a1")
        assert hb.get_dead_agents() == []

    def test_dead_agent(self):
        hb = AgentHeartbeater()
        hb.TIMEOUT_THRESHOLD = 0.01
        hb.register("a1")
        time.sleep(0.02)
        dead = hb.get_dead_agents()
        assert "a1" in dead


class TestCircuitBreaker:
    def test_closed_allows_calls(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        result = cb.call(lambda: 42)
        assert result == 42
        assert cb.state == "CLOSED"

    def test_opens_after_failures(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=10)

        def _fail():
            raise ValueError("fail")

        with pytest.raises(ValueError):
            cb.call(_fail)
        with pytest.raises(ValueError):
            cb.call(_fail)
        # 第三次应该触发 OPEN
        with pytest.raises(RuntimeError) as exc:
            cb.call(lambda: 42)
        assert "OPEN" in str(exc.value)
        assert cb.state == "OPEN"

    def test_half_open_recovery(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05)
        try:
            cb.call(lambda: (_[0] for _ in []).throw(ValueError("fail")))
        except Exception:
            pass
        assert cb.state == "OPEN"
        time.sleep(0.06)
        # 恢复后 CLOSED
        result = cb.call(lambda: 99)
        assert result == 99
        assert cb.state == "CLOSED"


class TestSwarmExecutor:
    def test_callable_interface(self):
        class FakeController:
            def execute_nodes(self, nodes, ctx):
                return {n.id: {"status": "ok"} for n in nodes}

        controller = FakeController()
        executor = SwarmExecutor(controller)
        from xagent.core.workflow.models import TaskNode
        nodes = [TaskNode(id="a", goal="test")]
        ctx = object()
        results = executor(nodes, ctx)
        assert "a" in results
        assert results["a"]["status"] == "ok"


class TestSwarmControllerDisabled:
    def test_disabled_submit_raises(self):
        from xagent.core.swarm import SwarmController
        ctrl = SwarmController(enabled=False)
        with pytest.raises(RuntimeError, match="not enabled"):
            ctrl.submit({"node_id": "x"})

    def test_disabled_context_manager(self):
        from xagent.core.swarm import SwarmController
        ctrl = SwarmController(enabled=False)
        with ctrl as c:
            assert c is ctrl
            assert c._pool is None
