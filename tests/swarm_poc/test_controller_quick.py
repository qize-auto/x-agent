"""
SwarmController 快速功能测试
=============================
验证单机 spawn 模式下基本任务分发与结果收集。
注意：必须在 if __name__ == "__main__" 下运行（spawn 要求）。
"""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from xagent.core.swarm import SwarmController, CheckpointStore


def test_basic_submit():
    with tempfile.TemporaryDirectory() as tmpdir:
        ctrl = SwarmController(
            num_workers=2,
            config={"model": {"provider": "mock"}},
            checkpoint_dir=Path(tmpdir),
            enabled=True,
        )
        result = ctrl.submit({
            "node_id": "test_1",
            "goal": "Say hello",
            "mode": "plan",
        })
        ctrl.shutdown()

        assert result["status"] == "completed", f"Unexpected status: {result}"
        assert "pid" in result
        assert result["pid"] != 0
        print(f"[PASS] basic_submit: {result['status']} pid={result['pid']}")


def test_batch_submit():
    with tempfile.TemporaryDirectory() as tmpdir:
        ctrl = SwarmController(
            num_workers=2,
            config={"model": {"provider": "mock"}},
            checkpoint_dir=Path(tmpdir),
            enabled=True,
        )
        tasks = [
            {"node_id": f"batch_{i}", "goal": f"Task {i}", "mode": "plan"}
            for i in range(4)
        ]
        results = ctrl.submit_many(tasks)
        ctrl.shutdown()

        assert len(results) == 4
        completed = sum(1 for r in results if r["status"] == "completed")
        assert completed == 4, f"Only {completed}/4 completed"
        pids = {r["pid"] for r in results}
        print(f"[PASS] batch_submit: {completed}/4 OK, PIDs: {pids}")


def test_disabled():
    ctrl = SwarmController(num_workers=2, enabled=False)
    try:
        ctrl.submit({"node_id": "x", "goal": "test"})
        assert False, "Should raise when disabled"
    except RuntimeError as e:
        assert "not enabled" in str(e)
        print("[PASS] disabled_guard")


def test_checkpoint_persist():
    with tempfile.TemporaryDirectory() as tmpdir:
        ctrl = SwarmController(
            num_workers=2,
            config={"model": {"provider": "mock"}},
            checkpoint_dir=Path(tmpdir),
            enabled=True,
        )
        ctrl.submit({"node_id": "cp_test", "goal": "checkpoint", "mode": "plan"})
        ctrl.shutdown()

        cps = ctrl.get_checkpoints()
        assert len(cps) >= 1
        print(f"[PASS] checkpoint_persist: {len(cps)} checkpoints saved")


def main():
    print("=" * 50)
    print("SwarmController 快速功能测试")
    print("=" * 50)
    test_disabled()
    test_basic_submit()
    test_batch_submit()
    test_checkpoint_persist()
    print("=" * 50)
    print("All quick tests PASSED")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
