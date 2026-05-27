"""
阶段0-3b: 真实 AgentLoop 初始化时间测量
=======================================
测量从空解释器到 AgentLoop 可执行任务的总时间，
这是 spawn 模式下每个 Worker 的真实启动开销。
"""
from __future__ import annotations
import sys
import time
import psutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def measure():
    proc = psutil.Process()
    mem0 = proc.memory_info().rss / (1024 * 1024)
    t0 = time.time()

    # 1. 导入核心模块（spawn 模式下 Worker 需要重新 import）
    t1 = time.time()
    from xagent.core.agent_loop import AgentLoop
    import_time = time.time() - t1
    mem1 = proc.memory_info().rss / (1024 * 1024)

    # 2. 创建 Mock 依赖（避免真实 ChromaDB / 工具初始化）
    from unittest.mock import MagicMock
    class MockLLM:
        model_id = "mock/model"
        provider = "mock"
        def chat(self, messages, **kwargs):
            resp = type("FakeResp", (), {})()
            resp.content = "mock response"
            resp.tool_calls = []
            resp.reasoning = ""
            resp.usage = {}
            return resp

    # 3. 创建最小 AgentLoop
    t2 = time.time()
    llm = MockLLM()
    tools = MagicMock()
    tools.list_tools.return_value = []
    tools.get_schemas.return_value = []
    memory = MagicMock()
    memory.recall.return_value = []
    memory.add.return_value = None
    memory.stats.return_value = "mock"
    agent = AgentLoop(
        llm=llm,
        tools=tools,
        memory=memory,
        project_root=str(Path(__file__).parent.parent.parent),
        config={
            "persistence": {"auto_checkpoint": False},
            "adaptive": {"auto_throttle": False, "enabled": False},
            "vision": {"enabled": False},
            "clarification": {"enabled": False},
        },
    )
    init_time = time.time() - t2
    mem2 = proc.memory_info().rss / (1024 * 1024)

    total = time.time() - t0

    print(f"Import 时间:  {import_time:.3f}s")
    print(f"Import 内存:  {mem1 - mem0:.1f} MB")
    print(f"Init 时间:    {init_time:.3f}s")
    print(f"Init 内存:    {mem2 - mem1:.1f} MB")
    print(f"总时间:       {total:.3f}s")
    print(f"总内存:       {mem2 - mem0:.1f} MB")

    # 4. 快速执行一个任务（验证可用性）
    t3 = time.time()
    plan = agent.plan_task("Say hello")
    plan_time = time.time() - t3
    print(f"Plan 时间:    {plan_time:.3f}s")
    print(f"Plan 子任务:  {plan.total_count()}")

    return {
        "import_time": round(import_time, 3),
        "init_time": round(init_time, 3),
        "total_time": round(total, 3),
        "plan_time": round(plan_time, 3),
        "memory_mb": round(mem2 - mem0, 1),
    }


if __name__ == "__main__":
    print("=" * 50)
    print("AgentLoop 真实初始化时间测量")
    print("=" * 50)
    result = measure()
    print("=" * 50)
    print(f"结论: 单个 Worker 初始化约 {result['total_time']:.1f}s, 内存 {result['memory_mb']:.0f}MB")
    print("=" * 50)
