"""自主调度器

根据用户习惯智能调度任务执行时间。
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .worker import BackgroundWorker, WorkerResult
from ..persistence.task_store import TaskStore


@dataclass
class ScheduledTask:
    """调度任务"""
    task_id: str
    goal: str
    strategy: str  # immediate | night | interval
    target: Callable
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    scheduled_at: float = field(default_factory=time.time)
    execute_after: float = 0.0  # 时间戳，0 表示立即


class AutonomousScheduler:
    """
    自主任务调度器。

    用法:
        scheduler = AutonomousScheduler(agent_loop)
        scheduler.schedule("帮我跑 benchmark", strategy="night")
        # 晚上自动执行，早上汇报结果
    """

    def __init__(self, task_store: TaskStore = None):
        self.task_store = task_store or TaskStore()
        self.worker = BackgroundWorker()
        self._queue: list[ScheduledTask] = []
        self._user_patterns = {
            "active_hours": (9, 22),      # 默认活跃时段 9:00-22:00
            "night_start": 23,            # 夜间任务开始时间
            "night_end": 7,               # 夜间任务结束时间
        }

    def schedule(self, goal: str, target: Callable, args: tuple = (),
                 kwargs: dict = None, strategy: str = "immediate") -> str:
        """
        调度一个任务。

        Args:
            strategy: "immediate" — 有空闲时立即执行
                     "night" — 夜间低负载时执行
                     "interval" — 间歇执行（每 30min checkpoint）
        """
        kwargs = kwargs or {}
        task_id = f"sch_{int(time.time() * 1000)}"
        execute_after = 0.0

        if strategy == "night":
            execute_after = self._next_night_window()
        elif strategy == "interval":
            execute_after = time.time() + 1800  # 30 分钟后开始

        st = ScheduledTask(
            task_id=task_id,
            goal=goal,
            strategy=strategy,
            target=target,
            args=args,
            kwargs=kwargs,
            execute_after=execute_after,
        )

        # immediate 策略：直接启动，不加入队列
        if strategy == "immediate":
            self._launch(st)
        else:
            self._queue.append(st)

        return task_id

    def tick(self) -> list[str]:
        """
        调度心跳，检查是否有到期的任务需要执行。
        应由外部定时调用（如每 5 分钟）。

        Returns:
            本次启动的任务 ID 列表
        """
        now = time.time()
        launched = []
        remaining = []

        for st in self._queue:
            if st.execute_after <= now:
                self._launch(st)
                launched.append(st.task_id)
            else:
                remaining.append(st)

        self._queue = remaining
        return launched

    def _launch(self, st: ScheduledTask):
        """启动后台执行"""
        def on_complete(result: WorkerResult):
            # 保存结果到持久化存储
            if self.task_store:
                self.task_store.save_state(
                    st.task_id,
                    {"status": result.status, "duration": result.duration, "error": result.error},
                )

        self.worker.start(
            task_id=st.task_id,
            target=st.target,
            args=st.args,
            kwargs=st.kwargs,
            on_complete=on_complete,
        )

    def _next_night_window(self) -> float:
        """计算下一个夜间窗口的开始时间"""
        import datetime
        now = datetime.datetime.now()
        night_start = self._user_patterns["night_start"]

        if now.hour >= night_start:
            # 今晚已经过了夜间开始时间，安排到明天晚上
            target = now + datetime.timedelta(days=1)
        else:
            target = now

        target = target.replace(hour=night_start, minute=0, second=0, microsecond=0)
        return target.timestamp()

    def get_queue(self) -> list[dict]:
        """获取等待中的任务列表"""
        return [
            {
                "task_id": st.task_id,
                "goal": st.goal,
                "strategy": st.strategy,
                "execute_after": st.execute_after,
            }
            for st in self._queue
        ]

    def cancel_queued(self, task_id: str) -> bool:
        """取消等待中的任务"""
        for i, st in enumerate(self._queue):
            if st.task_id == task_id:
                self._queue.pop(i)
                return True
        return False
