"""
SwarmController
===============
多进程 Swarm 控制器。

职责：
1. 维护 multiprocessing.Pool（spawn 模式兼容 Windows）
2. 任务分发与结果收集
3. Worker 心跳监控（轻量）
4. 故障检测与自动重试
"""
from __future__ import annotations
import multiprocessing as mp
import os
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Optional, Callable

from .checkpoint import CheckpointStore, SwarmCheckpoint
from .worker import _worker_init, _worker_execute
from .shared_index import SharedIndexManager


class AgentHeartbeater:
    """轻量心跳检测器（单线程，不启动额外线程）"""

    HEARTBEAT_INTERVAL = 5   # 秒
    TIMEOUT_THRESHOLD = 15   # 秒

    def __init__(self):
        self.last_seen: dict[str, float] = {}

    def register(self, agent_id: str):
        self.last_seen[agent_id] = time.time()

    def heartbeat(self, agent_id: str):
        self.last_seen[agent_id] = time.time()

    def get_dead_agents(self) -> list[str]:
        now = time.time()
        return [
            aid for aid, ts in self.last_seen.items()
            if now - ts > self.TIMEOUT_THRESHOLD
        ]


class CircuitBreaker:
    """LLM/API 调用熔断器（简化版）"""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = "CLOSED"  # CLOSED | OPEN | HALF_OPEN
        self.last_failure_time: Optional[float] = None

    def call(self, fn: Callable, *args, **kwargs):
        if self.state == "OPEN":
            if self.last_failure_time and (time.time() - self.last_failure_time > self.recovery_timeout):
                self.state = "HALF_OPEN"
            else:
                raise RuntimeError("Circuit breaker OPEN")
        try:
            result = fn(*args, **kwargs)
            if self.state == "HALF_OPEN":
                self.state = "CLOSED"
                self.failure_count = 0
            return result
        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = "OPEN"
            raise


class SwarmController:
    """
    Swarm 多进程控制器。

    默认关闭（enabled=False），零侵入现有代码。
    启用后替换 WorkflowEngine 中的 ThreadPoolExecutor。
    """

    def __init__(
        self,
        num_workers: int = 2,
        config: dict | None = None,
        project_root: str = "",
        checkpoint_dir: Path | None = None,
        redis_url: str | None = None,
        enabled: bool = False,
    ):
        self.enabled = enabled
        self.num_workers = max(1, num_workers)
        self.config = config or {}
        self.project_root = project_root
        self._pool: Optional[mp.pool.Pool] = None
        self._ctx = mp.get_context("spawn")
        self._heartbeater = AgentHeartbeater()
        self._circuit = CircuitBreaker()
        self._shm_name: Optional[str] = None
        self._shm_obj = None

        # Checkpoint
        cp_dir = checkpoint_dir or (Path.home() / ".xagent" / "swarm_checkpoints")
        self._checkpoint_store = CheckpointStore(cp_dir, redis_url)

        # 统计
        self.stats = {
            "submitted": 0,
            "completed": 0,
            "failed": 0,
            "retried": 0,
        }

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> "SwarmController":
        """启动进程池（惰性：仅在需要时创建）"""
        if not self.enabled:
            return self
        if self._pool is not None:
            return self

        # 可选：预加载代码索引到共享内存
        shm_name = None
        if self.config.get("swarm", {}).get("preload_index", False) and self.project_root:
            try:
                from ..code_intel.indexer import CodeIndexer
                max_files = self.config.get("_adaptive", {}).get("max_index_files")
                indexer = CodeIndexer(self.project_root, max_files=max_files)
                indexer.index_all()
                shm_name, shm_obj = SharedIndexManager.put_indexer(indexer)
                self._shm_name = shm_name
                self._shm_obj = shm_obj
            except Exception:
                pass

        self._pool = self._ctx.Pool(
            processes=self.num_workers,
            initializer=_worker_init,
            initargs=(self.config, self.project_root, shm_name),
        )

        # 可选：自动预热（避免首个真实任务承担初始化延迟）
        if self.config.get("swarm", {}).get("auto_warmup", False):
            self.warmup()

        return self

    def warmup(self) -> dict:
        """
        进程池预热：发送空任务强制所有 Worker 完成延迟初始化。
        返回预热耗时和每个 Worker 的 PID。
        """
        if not self.enabled or self._pool is None:
            raise RuntimeError("Pool not started")
        import time
        t0 = time.time()
        # 发送与 Worker 数量相同的空任务，强制每个 Worker 初始化
        tasks = [{"node_id": f"warmup_{i}", "goal": "warmup", "mode": "plan"} for i in range(self.num_workers)]
        results = self._pool.starmap(
            _worker_execute,
            [(t, self.config, self.project_root) for t in tasks],
        )
        elapsed = time.time() - t0
        pids = {r.get("pid", "?") for r in results if isinstance(r, dict)}
        return {
            "elapsed_sec": round(elapsed, 2),
            "workers_warmed": len(results),
            "pids": sorted(pids),
        }

    def shutdown(self, wait: bool = True):
        """优雅关闭"""
        if self._pool is not None:
            self._pool.close()
            if wait:
                self._pool.join()
            self._pool = None
        if self._shm_name:
            SharedIndexManager.cleanup(self._shm_name, self._shm_obj)
            self._shm_name = None
            self._shm_obj = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.shutdown()
        return False

    # ------------------------------------------------------------------
    # 任务提交
    # ------------------------------------------------------------------

    def submit(self, task_dict: dict) -> dict:
        """
        同步提交单个任务，等待结果返回。
        支持自动重试（按 swarm.retry 配置）。
        返回包含 result / error / status 的 dict。
        """
        if not self.enabled:
            raise RuntimeError("Swarm not enabled")
        self.start()
        self.stats["submitted"] += 1

        node_id = task_dict.get("node_id", "unknown")
        cp_id = f"{node_id}_{int(time.time() * 1000)}"
        swarm_cfg = self.config.get("swarm", {})
        timeout = swarm_cfg.get("task_timeout_sec", 3600)
        max_retries = swarm_cfg.get("retry", {}).get("max_retries", 3)
        backoff = swarm_cfg.get("retry", {}).get("backoff_factor", 2.0)

        # 保存 pending checkpoint
        self._checkpoint_store.save(SwarmCheckpoint(
            checkpoint_id=cp_id,
            task_id=node_id,
            node_id=node_id,
            status="pending",
            created_at=time.time(),
            updated_at=time.time(),
        ))

        last_error = None
        for attempt in range(max_retries + 1):
            if attempt > 0:
                self.stats["retried"] += 1
                delay = (backoff ** (attempt - 1))
                time.sleep(min(delay, 30))  # 最多等 30s

            def _run():
                if self._pool is None:
                    raise RuntimeError("Pool not started")
                async_result = self._pool.apply_async(
                    _worker_execute,
                    args=(task_dict, self.config, self.project_root),
                )
                return async_result.get(timeout=timeout)

            try:
                result = self._circuit.call(_run)
                # 心跳：记录 Worker PID 活跃
                pid = result.get("pid", "unknown")
                self._heartbeater.heartbeat(f"worker_{pid}")
                self.stats["completed"] += 1
                self._checkpoint_store.save(SwarmCheckpoint(
                    checkpoint_id=cp_id,
                    task_id=node_id,
                    node_id=node_id,
                    status="completed",
                    created_at=time.time(),
                    updated_at=time.time(),
                    result=result,
                    retry_count=attempt,
                ))
                return result

            except FutureTimeoutError:
                last_error = f"timeout_after_{timeout}s"
                self._checkpoint_store.save(SwarmCheckpoint(
                    checkpoint_id=cp_id,
                    task_id=node_id,
                    node_id=node_id,
                    status="running",
                    created_at=time.time(),
                    updated_at=time.time(),
                    error=f"attempt {attempt + 1}/{max_retries + 1}: {last_error}",
                    retry_count=attempt + 1,
                ))

            except Exception as e:
                last_error = str(e)
                self._checkpoint_store.save(SwarmCheckpoint(
                    checkpoint_id=cp_id,
                    task_id=node_id,
                    node_id=node_id,
                    status="running",
                    created_at=time.time(),
                    updated_at=time.time(),
                    error=f"attempt {attempt + 1}/{max_retries + 1}: {last_error}",
                    retry_count=attempt + 1,
                ))

        # 所有重试耗尽
        self.stats["failed"] += 1
        self._checkpoint_store.save(SwarmCheckpoint(
            checkpoint_id=cp_id,
            task_id=node_id,
            node_id=node_id,
            status="failed",
            created_at=time.time(),
            updated_at=time.time(),
            error=last_error,
            retry_count=max_retries,
        ))
        return {
            "node_id": node_id,
            "status": "failed",
            "error": last_error,
            "retries": max_retries,
        }

    def submit_many(self, task_dicts: list[dict]) -> list[dict]:
        """批量提交，保持顺序返回"""
        if not self.enabled:
            raise RuntimeError("Swarm not enabled")
        self.start()
        return [self.submit(td) for td in task_dicts]

    # ------------------------------------------------------------------
    # 工作流集成
    # ------------------------------------------------------------------

    def execute_nodes(self, nodes: list, ctx) -> dict:
        """
        并行执行多个 WorkflowNode，返回 node_id -> result 的映射。
        签名与 WorkflowEngine._execute_parallel 兼容。
        """
        if not self.enabled:
            raise RuntimeError("Swarm not enabled")
        self.start()

        from xagent.core.workflow.models import TaskNode

        task_dicts = []
        for node in nodes:
            if isinstance(node, TaskNode):
                task_dicts.append({
                    "node_id": node.id,
                    "goal": node.goal,
                    "mode": "plan",
                })
            else:
                # 非任务节点直接本地执行
                task_dicts.append({
                    "node_id": node.id,
                    "goal": getattr(node, "condition", "non-task"),
                    "mode": "plan",
                })

        results = {}
        for td in task_dicts:
            results[td["node_id"]] = self.submit(td)
        return results

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        return dict(self.stats)

    def get_checkpoints(self) -> list:
        return self._checkpoint_store.list_all()

    def cleanup(self, max_age_sec: float = 86400) -> int:
        return self._checkpoint_store.cleanup_old(max_age_sec)
