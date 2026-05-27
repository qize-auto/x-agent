"""Tests for swarm advanced features (worker + scheduler)."""
import time

import pytest

from xagent.core.swarm.worker import BackgroundWorker, WorkerResult
from xagent.core.swarm.scheduler import AutonomousScheduler, ScheduledTask


class TestBackgroundWorker:
    def test_run_and_complete(self):
        worker = BackgroundWorker()
        results = []

        def target(x):
            time.sleep(0.05)
            return x * 2

        def on_complete(result: WorkerResult):
            results.append(result)

        worker.start("t1", target, args=(21,), on_complete=on_complete)
        time.sleep(0.15)

        assert len(results) == 1
        assert results[0].status == "completed"
        assert results[0].plan == 42
        assert results[0].duration > 0

    def test_run_failure(self):
        worker = BackgroundWorker()
        results = []

        def target():
            raise ValueError("oops")

        worker.start("t2", target, on_complete=lambda r: results.append(r))
        time.sleep(0.1)

        assert len(results) == 1
        assert results[0].status == "failed"
        assert "oops" in results[0].error

    def test_is_running(self):
        worker = BackgroundWorker()

        def target():
            time.sleep(0.3)

        worker.start("t3", target)
        assert worker.is_running("t3")
        time.sleep(0.4)
        assert not worker.is_running("t3")

    def test_get_result(self):
        worker = BackgroundWorker()

        def target():
            return 123

        worker.start("t4", target)
        time.sleep(0.1)

        result = worker.get_result("t4")
        assert result is not None
        assert result.status == "completed"

    def test_cancel(self):
        worker = BackgroundWorker()
        worker.start("t5", lambda: time.sleep(10))
        assert worker.cancel("t5") is True
        assert worker.get_result("t5").status == "cancelled"

    def test_list_running(self):
        worker = BackgroundWorker()
        worker.start("t6", lambda: time.sleep(0.5))
        running = worker.list_running()
        assert "t6" in running


class TestAutonomousScheduler:
    def test_schedule_immediate(self):
        executed = []

        def target():
            executed.append(1)
            return "done"

        sch = AutonomousScheduler()
        tid = sch.schedule("test goal", target, strategy="immediate")
        time.sleep(0.15)

        assert len(executed) == 1
        assert not sch.get_queue()  # immediate 已移出队列

    def test_schedule_queued(self):
        sch = AutonomousScheduler()
        tid = sch.schedule("future", lambda: None, strategy="night")
        queue = sch.get_queue()
        assert len(queue) == 1
        assert queue[0]["task_id"] == tid
        assert queue[0]["strategy"] == "night"

    def test_cancel_queued(self):
        sch = AutonomousScheduler()
        tid = sch.schedule("future", lambda: None, strategy="night")
        assert sch.cancel_queued(tid) is True
        assert len(sch.get_queue()) == 0
        assert sch.cancel_queued("noexist") is False

    def test_tick_launches_due_tasks(self):
        executed = []

        def target():
            executed.append(1)
            return "done"

        sch = AutonomousScheduler()
        # 手动插入一个已过期的任务
        st = ScheduledTask(
            task_id="due",
            goal="due task",
            strategy="interval",
            target=target,
            execute_after=time.time() - 1,  # 已过期
        )
        sch._queue.append(st)

        launched = sch.tick()
        time.sleep(0.15)

        assert "due" in launched
        assert len(executed) == 1
        assert len(sch.get_queue()) == 0

    def test_tick_skips_future_tasks(self):
        sch = AutonomousScheduler()
        st = ScheduledTask(
            task_id="future",
            goal="future task",
            strategy="night",
            target=lambda: None,
            execute_after=time.time() + 3600,
        )
        sch._queue.append(st)

        launched = sch.tick()
        assert len(launched) == 0
        assert len(sch.get_queue()) == 1

    def test_next_night_window(self):
        sch = AutonomousScheduler()
        ts = sch._next_night_window()
        assert ts > time.time()  # 应该是未来时间
