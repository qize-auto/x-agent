"""
阶段2-4: Swarm 性能基准测试
============================
对比单线程 vs 多进程 Swarm 在不同任务负载下的表现。

场景：6 节点 DAG（2 批次 × 3 并行）
- Batch 1: 3 个无依赖节点
- Batch 2: 3 个依赖 Batch 1 的节点

对比维度：
1. 单线程 ThreadPoolExecutor（baseline）
2. Swarm 2 Workers
3. Swarm 4 Workers
"""
from __future__ import annotations
import multiprocessing as mp
import sys
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from xagent.core.workflow.models import Workflow, TaskNode, EndNode
from xagent.core.workflow.engine import WorkflowEngine
from xagent.core.swarm import SwarmController, SwarmExecutor


def _build_test_workflow() -> Workflow:
    """构建测试 DAG"""
    return Workflow(
        name="benchmark",
        entry="a",
        nodes={
            "a": TaskNode(id="a", goal="Task A"),
            "b": TaskNode(id="b", goal="Task B"),
            "c": TaskNode(id="c", goal="Task C"),
            "d": TaskNode(id="d", goal="Task D", depends_on=["a", "b", "c"]),
            "e": TaskNode(id="e", goal="Task E", depends_on=["a", "b", "c"]),
            "f": TaskNode(id="f", goal="Task F", depends_on=["d", "e"]),
            "end": EndNode(id="end", depends_on=["f"]),
        },
    )


def benchmark_single_thread(wf: Workflow) -> dict:
    """单线程 baseline"""
    engine = WorkflowEngine(agent_loop=None)
    t0 = time.time()
    ctx = engine.run(wf)
    elapsed = time.time() - t0
    return {
        "mode": "single_thread",
        "elapsed_sec": round(elapsed, 3),
        "executed": len(ctx.executed_nodes),
    }


def benchmark_swarm(wf: Workflow, workers: int) -> dict:
    """Swarm 多进程"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        ctrl = SwarmController(
            num_workers=workers,
            config={"model": {"provider": "mock"}},
            checkpoint_dir=Path(tmpdir),
            enabled=True,
        )
        executor = SwarmExecutor(ctrl)
        engine = WorkflowEngine(agent_loop=None, executor=executor)

        t0 = time.time()
        ctx = engine.run(wf)
        elapsed = time.time() - t0

        stats = ctrl.get_stats()
        ctrl.shutdown()

        return {
            "mode": f"swarm_{workers}w",
            "elapsed_sec": round(elapsed, 3),
            "executed": len(ctx.executed_nodes),
            "stats": stats,
        }


def main():
    print("=" * 60)
    print("Swarm 性能基准测试")
    print(f"Python: {sys.version}")
    print(f"Start method: {mp.get_start_method()}")
    print(f"CPU cores: {mp.cpu_count()}")
    print("=" * 60)

    wf = _build_test_workflow()
    print(f"测试工作流: {len(wf.nodes)} 节点")
    print()

    results = []

    # 1. 单线程
    print("[1/3] 单线程 ThreadPoolExecutor ...")
    r1 = benchmark_single_thread(wf)
    results.append(r1)
    print(f"      耗时: {r1['elapsed_sec']:.3f}s")

    # 2. Swarm 2 Workers
    print("[2/3] Swarm 2 Workers ...")
    r2 = benchmark_swarm(wf, 2)
    results.append(r2)
    print(f"      耗时: {r2['elapsed_sec']:.3f}s")

    # 3. Swarm 4 Workers
    print("[3/3] Swarm 4 Workers ...")
    r3 = benchmark_swarm(wf, 4)
    results.append(r3)
    print(f"      耗时: {r3['elapsed_sec']:.3f}s")

    # 汇总
    print("\n" + "=" * 60)
    print("结果汇总")
    print("=" * 60)
    baseline = results[0]["elapsed_sec"]
    for r in results:
        speedup = baseline / r["elapsed_sec"] if r["elapsed_sec"] > 0 else 0
        print(f"  {r['mode']:20s}  {r['elapsed_sec']:7.3f}s  speedup={speedup:.2f}x")
    print("=" * 60)

    # 保存报告
    report_path = Path(__file__).parent / "benchmark_report.json"
    report_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"报告已保存: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
