"""
阶段0-2/3: spawn 模式最小原型 + 性能测量
======================================
验证 Windows spawn 模式下多进程执行的可行性，测量：
- 进程池启动时间
- 单任务执行时间
- 内存占用变化
- CPU 负载

设计约束：
- Worker 函数必须定义在模块顶层（spawn 要求）
- 不传递不可 pickle 的对象（如 AgentLoop）
- 使用纯数据（dict / dataclass）传递任务
"""
from __future__ import annotations
import multiprocessing as mp
import os
import sys
import time
import json
import psutil
from pathlib import Path
from dataclasses import dataclass, asdict

# 添加项目根到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from xagent.core.workflow.models import WorkflowContext, TaskNode, Workflow


# ========================================================================
# Worker 函数（必须模块顶层，spawn 模式要求）
# ========================================================================

_WORKER_STATE = {}  # 每个进程独立的延迟初始化缓存


def _worker_init(config: dict):
    """Worker 进程初始化（由 Pool 调用）"""
    _WORKER_STATE["pid"] = os.getpid()
    _WORKER_STATE["start_time"] = time.time()
    _WORKER_STATE["config"] = config
    # 模拟 AgentLoop 的延迟初始化开销
    time.sleep(0.1)  # 模拟加载模块/初始化


def _worker_execute(task: dict) -> dict:
    """
    Worker 执行函数 —— 纯计算任务，不调用 LLM。
    参数和返回值都必须是可 pickle 的 dict。
    """
    pid = _WORKER_STATE.get("pid", os.getpid())
    start = time.time()

    goal = task.get("goal", "")
    task_id = task.get("id", "unknown")

    # 模拟不同复杂度的任务
    complexity = task.get("complexity", 1)
    # 纯 CPU 计算：计算斐波那契数列（避免 I/O，纯测量 CPU 并行效率）
    def fib(n):
        if n <= 1:
            return n
        return fib(n - 1) + fib(n - 2)

    result_val = fib(25 + complexity * 2)  # ~0.05s per task at complexity=1

    elapsed = time.time() - start

    return {
        "task_id": task_id,
        "pid": pid,
        "goal": goal,
        "result": result_val,
        "elapsed_sec": round(elapsed, 4),
        "worker_start_delay": round(start - _WORKER_STATE.get("start_time", start), 4),
    }


def _worker_execute_with_context(task_dict: dict, ctx_dict: dict) -> dict:
    """带 WorkflowContext 的 Worker 执行（更接近真实场景）"""
    pid = _WORKER_STATE.get("pid", os.getpid())
    start = time.time()

    # 从 dict 重建 WorkflowContext
    ctx = WorkflowContext(
        variables=ctx_dict.get("variables", {}),
        node_results=ctx_dict.get("node_results", {}),
        executed_nodes=set(ctx_dict.get("executed_nodes", [])),
        failed_nodes=set(ctx_dict.get("failed_nodes", [])),
    )

    goal = task_dict.get("goal", "")
    task_id = task_dict.get("id", "unknown")

    # 模拟任务执行
    result_text = f"Processed: {goal}"
    ctx.node_results[task_id] = {"status": "completed", "output": result_text}
    ctx.executed_nodes.add(task_id)

    elapsed = time.time() - start

    return {
        "task_id": task_id,
        "pid": pid,
        "goal": goal,
        "ctx_executed_count": len(ctx.executed_nodes),
        "elapsed_sec": round(elapsed, 4),
    }


# ========================================================================
# 测量工具
# ========================================================================

class PerformanceMonitor:
    """轻量级性能监控"""

    def __init__(self):
        self.process = psutil.Process()
        self.start_mem = self.process.memory_info().rss / (1024 * 1024)
        self.start_cpu = psutil.cpu_percent(interval=None)

    def snapshot(self, label: str = "") -> dict:
        mem = self.process.memory_info().rss / (1024 * 1024)
        cpu = psutil.cpu_percent(interval=0.1)
        return {
            "label": label,
            "memory_mb": round(mem, 1),
            "memory_delta_mb": round(mem - self.start_mem, 1),
            "cpu_percent": round(cpu, 1),
        }


# ========================================================================
# 测试场景
# ========================================================================

def scenario_1_basic_spawn():
    """场景1：基础 spawn —— 2 个 Worker 执行 4 个任务"""
    print("\n" + "=" * 60)
    print("场景1: 基础 spawn (2 Workers, 4 Tasks)")
    print("=" * 60)

    monitor = PerformanceMonitor()
    print(f"  主进程 PID: {os.getpid()}")
    print(f"  初始内存: {monitor.start_mem:.1f} MB")

    config = {"model": "mock", "timeout": 30}

    # 使用 spawn context（Windows 默认/唯一选择）
    ctx = mp.get_context("spawn")

    t0 = time.time()
    pool = ctx.Pool(
        processes=2,
        initializer=_worker_init,
        initargs=(config,),
    )
    pool_startup = time.time() - t0
    print(f"  Pool 启动时间: {pool_startup:.2f}s")
    print(f"  启动后内存: {monitor.snapshot('after_pool_startup')['memory_mb']:.1f} MB")

    tasks = [
        {"id": f"t{i}", "goal": f"Task {i}", "complexity": i % 3}
        for i in range(4)
    ]

    t1 = time.time()
    results = pool.map(_worker_execute, tasks)
    map_time = time.time() - t1

    pool.close()
    pool.join()

    total_time = time.time() - t0

    print(f"\n  任务执行结果:")
    for r in results:
        print(f"    {r['task_id']}: pid={r['pid']}, elapsed={r['elapsed_sec']:.3f}s")

    print(f"\n  性能汇总:")
    print(f"    Pool 启动: {pool_startup:.2f}s")
    print(f"    map 执行:  {map_time:.3f}s")
    print(f"    总耗时:    {total_time:.2f}s")
    print(f"    当前内存:  {monitor.snapshot('after_completion')['memory_mb']:.1f} MB")

    return {
        "scenario": "basic_spawn",
        "workers": 2,
        "tasks": 4,
        "pool_startup_sec": round(pool_startup, 2),
        "map_time_sec": round(map_time, 3),
        "total_time_sec": round(total_time, 2),
        "results": results,
    }


def scenario_2_workflow_simulation():
    """场景2：模拟 Workflow 并行节点执行"""
    print("\n" + "=" * 60)
    print("场景2: Workflow 并行节点模拟 (3 Workers, 6 Tasks)")
    print("=" * 60)

    monitor = PerformanceMonitor()
    config = {"model": "mock"}
    mp_ctx = mp.get_context("spawn")

    t0 = time.time()
    pool = mp_ctx.Pool(processes=3, initializer=_worker_init, initargs=(config,))
    pool_startup = time.time() - t0
    print(f"  Pool 启动时间: {pool_startup:.2f}s")

    # 模拟 DAG 的两个并行批次
    # Batch 1: 3 个无依赖节点并行
    batch1 = [
        {"id": "a", "goal": "Research topic A"},
        {"id": "b", "goal": "Research topic B"},
        {"id": "c", "goal": "Research topic C"},
    ]
    ctx1 = WorkflowContext().__dict__
    args1 = [(t, ctx1) for t in batch1]

    t1 = time.time()
    results1 = pool.starmap(_worker_execute_with_context, args1)
    batch1_time = time.time() - t1

    print(f"\n  Batch 1 (3 parallel): {batch1_time:.3f}s")
    for r in results1:
        print(f"    {r['task_id']}: pid={r['pid']}, elapsed={r['elapsed_sec']:.3f}s")

    # Batch 2: 3 个依赖 Batch 1 的节点并行
    ctx2 = WorkflowContext()
    for r in results1:
        ctx2.node_results[r["task_id"]] = r
        ctx2.executed_nodes.add(r["task_id"])
    ctx2_dict = ctx2.__dict__

    batch2 = [
        {"id": "d", "goal": "Write draft"},
        {"id": "e", "goal": "Review code"},
        {"id": "f", "goal": "Run tests"},
    ]
    args2 = [(t, ctx2_dict) for t in batch2]

    t2 = time.time()
    results2 = pool.starmap(_worker_execute_with_context, args2)
    batch2_time = time.time() - t2

    print(f"\n  Batch 2 (3 parallel): {batch2_time:.3f}s")
    for r in results2:
        print(f"    {r['task_id']}: pid={r['pid']}, elapsed={r['elapsed_sec']:.3f}s")

    pool.close()
    pool.join()
    total = time.time() - t0

    print(f"\n  性能汇总:")
    print(f"    Pool 启动: {pool_startup:.2f}s")
    print(f"    Batch 1:   {batch1_time:.3f}s")
    print(f"    Batch 2:   {batch2_time:.3f}s")
    print(f"    总耗时:    {total:.2f}s")
    print(f"    当前内存:  {monitor.snapshot('after_completion')['memory_mb']:.1f} MB")

    return {
        "scenario": "workflow_simulation",
        "workers": 3,
        "batches": 2,
        "pool_startup_sec": round(pool_startup, 2),
        "batch1_time_sec": round(batch1_time, 3),
        "batch2_time_sec": round(batch2_time, 3),
        "total_time_sec": round(total, 2),
    }


def scenario_3_stress_test():
    """场景3：压力测试 —— 大量轻量任务，测量吞吐"""
    print("\n" + "=" * 60)
    print("场景3: 压力测试 (4 Workers, 20 轻量 Tasks)")
    print("=" * 60)

    monitor = PerformanceMonitor()
    config = {"model": "mock"}
    mp_ctx = mp.get_context("spawn")

    t0 = time.time()
    pool = mp_ctx.Pool(processes=4, initializer=_worker_init, initargs=(config,))
    pool_startup = time.time() - t0
    print(f"  Pool 启动时间: {pool_startup:.2f}s")

    tasks = [{"id": f"s{i}", "goal": f"Step {i}", "complexity": 0} for i in range(20)]

    t1 = time.time()
    results = pool.map(_worker_execute, tasks)
    map_time = time.time() - t1

    pool.close()
    pool.join()
    total = time.time() - t0

    # 统计 PID 分布
    pid_counts = {}
    for r in results:
        pid_counts[r["pid"]] = pid_counts.get(r["pid"], 0) + 1

    print(f"\n  PID 分布: {pid_counts}")
    print(f"  平均单任务: {sum(r['elapsed_sec'] for r in results)/len(results):.4f}s")
    print(f"  map 总时间: {map_time:.3f}s")
    print(f"  吞吐: {len(results)/map_time:.1f} tasks/s")
    print(f"  当前内存: {monitor.snapshot('after_completion')['memory_mb']:.1f} MB")

    return {
        "scenario": "stress_test",
        "workers": 4,
        "tasks": 20,
        "pool_startup_sec": round(pool_startup, 2),
        "map_time_sec": round(map_time, 3),
        "total_time_sec": round(total, 2),
        "throughput": round(len(results) / map_time, 1),
        "pid_distribution": pid_counts,
    }


def main():
    print("=" * 60)
    print("spawn 模式最小原型 + 性能测量")
    print(f"Python: {sys.version}")
    print(f"Start method: {mp.get_start_method()}")
    print(f"CPU cores: {os.cpu_count()}")
    print("=" * 60)

    all_results = []

    all_results.append(scenario_1_basic_spawn())
    all_results.append(scenario_2_workflow_simulation())
    all_results.append(scenario_3_stress_test())

    # 汇总报告
    print("\n" + "=" * 60)
    print("综合报告")
    print("=" * 60)

    for r in all_results:
        print(f"\n  [{r['scenario']}]")
        for k, v in r.items():
            if k != "scenario" and k != "results" and k != "pid_distribution":
                print(f"    {k}: {v}")

    # 保存到文件
    report_path = Path(__file__).parent / "prototype_report.json"
    report_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  报告已保存: {report_path}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
