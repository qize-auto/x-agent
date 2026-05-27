"""
Agent 后台工作线程
=================
在独立 QThread 中运行 Agent 循环，通过信号与主 UI 通信。
"""
from __future__ import annotations
from PyQt6.QtCore import QThread, pyqtSignal


class AgentWorker(QThread):
    # 信号
    messageEmitted = pyqtSignal(str, object)   # (msg_type, payload)
    turnFinished = pyqtSignal(object)          # str (chat) 或 TaskPlan (task)
    errorOccurred = pyqtSignal(str)

    def __init__(self, agent_loop, user_input: str, mode: str = "chat", parent=None):
        super().__init__(parent)
        self.agent_loop = agent_loop
        self.user_input = user_input
        self.mode = mode  # "chat" | "task"

    def run(self):
        try:
            if self.mode == "plan":
                plan = self.agent_loop.plan_task(self.user_input)
                self.turnFinished.emit(plan)
            elif self.mode == "execute":
                # self.user_input 在这里存放 plan 的 JSON 序列化
                import json
                from xagent.core.task import TaskPlan, SubTask
                data = json.loads(self.user_input)
                plan = TaskPlan(goal=data["goal"])
                plan.subtasks = [SubTask(**st) for st in data.get("subtasks", [])]
                plan = self.agent_loop.execute_plan(plan)
                self.turnFinished.emit(plan)
            elif self.mode == "task":
                plan = self.agent_loop.run_task(self.user_input)
                self.turnFinished.emit(plan)
            else:
                result = self.agent_loop.run(self.user_input)
                self.turnFinished.emit(result)
        except Exception as e:
            err_msg = str(e)
            # 记录到 ErrorLedger（持久化 + 去重）
            try:
                from xagent.core.error_ledger import ErrorLedger
                ledger = ErrorLedger()
                ledger.record(
                    category="runtime",
                    message="Agent 执行异常",
                    detail=err_msg[:300],
                )
            except Exception:
                pass
            self.errorOccurred.emit(err_msg)
