"""
Swarm Worker 模块
=================
包含两部分：
1. BackgroundWorker / WorkerResult —— 旧版单机后台 Worker（供 scheduler 使用）
2. _worker_init / _worker_execute —— 多进程 spawn 兼容的 Worker 函数
"""
from __future__ import annotations
import os
import time
import threading
from dataclasses import dataclass
from typing import Optional, Callable


# ========================================================================
# 旧版接口：BackgroundWorker（供 scheduler / 测试使用）
# ========================================================================

@dataclass
class WorkerResult:
    """后台任务执行结果"""
    status: str           # completed | failed | cancelled
    plan: any = None      # 执行结果（兼容旧测试）
    duration: float = 0.0
    error: str = ""


class BackgroundWorker:
    """
    单机后台 Worker。
    用 threading 执行后台任务，供 AutonomousScheduler 使用。
    """

    def __init__(self):
        self._threads: dict[str, threading.Thread] = {}
        self._results: dict[str, WorkerResult] = {}
        self._lock = threading.Lock()

    def start(self, task_id: str, target: Callable, args: tuple = (),
              kwargs: dict = None, on_complete: Callable = None):
        """启动后台任务"""
        kwargs = kwargs or {}

        def _run():
            t0 = time.time()
            try:
                result = target(*args, **kwargs)
                duration = time.time() - t0
                wr = WorkerResult(status="completed", plan=result, duration=duration)
            except Exception as e:
                duration = time.time() - t0
                wr = WorkerResult(status="failed", error=str(e), duration=duration)

            with self._lock:
                self._results[task_id] = wr
                self._threads.pop(task_id, None)

            if on_complete:
                on_complete(wr)

        t = threading.Thread(target=_run, daemon=True)
        with self._lock:
            self._threads[task_id] = t
            self._results[task_id] = WorkerResult(status="running")
        t.start()

    def is_running(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._threads

    def get_result(self, task_id: str) -> Optional[WorkerResult]:
        with self._lock:
            return self._results.get(task_id)

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            if task_id not in self._threads:
                return False
            # 标记为取消（线程本身无法强制终止）
            self._results[task_id] = WorkerResult(status="cancelled")
            self._threads.pop(task_id, None)
            return True

    def list_running(self) -> list[str]:
        with self._lock:
            return list(self._threads.keys())


# ========================================================================
# 新版接口：多进程 spawn 兼容 Worker 函数
# 所有函数必须定义在模块顶层，以满足 Windows spawn 模式要求。
# ========================================================================

_WORKER_AGENT: Optional[object] = None
_WORKER_PID: int = 0
_WORKER_START_TIME: float = 0.0
_WORKER_SHM_NAME: Optional[str] = None


def _worker_init(config: dict, project_root: str = "", shm_name: str = None) -> dict:
    """
    Worker 进程初始化。
    由 Pool 的 initializer 调用，每个 Worker 进程仅执行一次。
    """
    global _WORKER_AGENT, _WORKER_PID, _WORKER_START_TIME, _WORKER_SHM_NAME
    _WORKER_PID = os.getpid()
    _WORKER_START_TIME = time.time()
    _WORKER_AGENT = None
    _WORKER_SHM_NAME = shm_name
    return {
        "pid": _WORKER_PID,
        "status": "ready",
        "init_delay_sec": 0.0,
    }


def _ensure_agent(config: dict, project_root: str = "") -> object:
    """确保 AgentLoop 已初始化（延迟加载）"""
    global _WORKER_AGENT
    if _WORKER_AGENT is not None:
        return _WORKER_AGENT

    from xagent.core.agent_loop import AgentLoop
    from xagent.core.llm_client import LLMClient
    from xagent.core.tool_registry import ToolRegistry
    from xagent.core.memory_engine import MemoryEngine

    llm_config = config.get("model", {})
    provider = llm_config.get("provider", "mock")

    if provider == "mock":
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

        llm = MockLLM()
    else:
        llm = LLMClient(
            provider=provider,
            model_id=llm_config.get("model_id", ""),
            api_key=llm_config.get("api_key", ""),
            base_url=llm_config.get("base_url", ""),
        )

    tools = ToolRegistry()
    persist_dir = config.get("memory", {}).get("persist_dir", "")
    if persist_dir:
        memory = MemoryEngine(persist_dir=persist_dir)
    else:
        from unittest.mock import MagicMock
        memory = MagicMock()
        memory.recall.return_value = []
        memory.add.return_value = None
        memory.stats.return_value = "mock"

    # 从共享内存加载预构建的代码索引（可选）
    code_indexer = None
    if _WORKER_SHM_NAME:
        try:
            from .shared_index import SharedIndexManager
            code_indexer = SharedIndexManager.load_indexer(_WORKER_SHM_NAME)
        except Exception:
            pass

    _WORKER_AGENT = AgentLoop(
        llm=llm,
        tools=tools,
        memory=memory,
        project_root=project_root or config.get("project_root", ""),
        config=config,
        code_indexer=code_indexer,
    )
    return _WORKER_AGENT


def _worker_execute(task_dict: dict, config: dict, project_root: str = "") -> dict:
    """
    Worker 执行函数 —— 执行单个任务节点。
    参数和返回值均为可 pickle 的 dict。
    """
    global _WORKER_PID, _WORKER_START_TIME
    pid = _WORKER_PID or os.getpid()
    t_start = time.time()
    node_id = task_dict.get("node_id", "unknown")
    goal = task_dict.get("goal", "")

    try:
        agent = _ensure_agent(config, project_root)
        mode = task_dict.get("mode", "plan")
        if mode == "plan":
            plan = agent.plan_task(goal)
            result = {
                "status": plan.status,
                "plan_id": plan.id,
                "subtask_count": plan.total_count(),
            }
        elif mode == "run":
            plan = agent.run_task(goal, mode="interactive")
            result = {
                "status": plan.status,
                "plan_id": plan.id,
                "subtask_count": plan.total_count(),
            }
        else:
            result = {"status": "unknown_mode", "mode": mode}

        elapsed = time.time() - t_start
        return {
            "node_id": node_id,
            "pid": pid,
            "status": "completed",
            "result": result,
            "elapsed_sec": round(elapsed, 3),
        }

    except Exception as e:
        elapsed = time.time() - t_start
        return {
            "node_id": node_id,
            "pid": pid,
            "status": "failed",
            "error": str(e),
            "elapsed_sec": round(elapsed, 3),
        }
