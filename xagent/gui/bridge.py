"""
JS ↔ Python 桥接
===============
暴露给前端调用的方法，用于对话输入、模型切换、配置读取等。
"""
from __future__ import annotations
import json
from PyQt6.QtCore import QObject, pyqtSlot


class AgentBridge(QObject):
    """前端 JS 通过 QWebChannel 调用 Python 方法"""

    def __init__(self, parent=None, agent_loop=None, config=None):
        super().__init__(parent)
        self._window = parent
        self.agent_loop = agent_loop
        self.config = config

    @pyqtSlot(str, result=str)
    def sendMessage(self, text: str) -> str:
        """用户发送消息 → Agent 处理（自动判断简单对话 vs 任务规划）"""
        if not self.agent_loop:
            return json.dumps({"ok": False, "error": "Agent 未初始化"})
        try:
            mode = self._detect_mode(text)
            if self._window and hasattr(self._window, "_run_agent"):
                self._window._run_agent(text, mode=mode)
            return json.dumps({"ok": True, "mode": mode})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})

    def _detect_mode(self, text: str) -> str:
        """启发式判断使用 chat 还是 plan 模式"""
        text_lower = text.lower().strip()
        task_keywords = [
            "帮我", "请帮我", "plan", "task", "步骤", "流程",
            "搭建", "创建项目", "重构", "migrate", "setup",
            "先", "然后", "接着", "最后", "第一步",
        ]
        if len(text) > 120:
            return "plan"
        if any(kw in text_lower for kw in task_keywords):
            return "plan"
        return "chat"

    @pyqtSlot(str, result=str)
    def executePlan(self, plan_json: str) -> str:
        """前端确认计划后，执行已有计划"""
        if not self.agent_loop:
            return json.dumps({"ok": False, "error": "Agent 未初始化"})
        try:
            if self._window and hasattr(self._window, "_execute_plan"):
                self._window._execute_plan(plan_json)
            return json.dumps({"ok": True})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})

    @pyqtSlot(str, result=str)
    def switchModel(self, preset_name: str) -> str:
        """切换模型预设"""
        try:
            if self.config:
                self.config.set_model_preset(preset_name)
                return json.dumps({"ok": True, "model": self.config.model.get("model_id")})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})
        return json.dumps({"ok": False, "error": "无配置"})

    @pyqtSlot(result=str)
    def getModelList(self) -> str:
        """获取可用模型预设列表"""
        if self.config:
            return json.dumps({"ok": True, "presets": self.config.list_model_presets()})
        return json.dumps({"ok": False, "presets": []})

    @pyqtSlot(result=str)
    def perceiveScreen(self) -> str:
        """截图并执行视觉感知，返回 UI 上下文字符串"""
        if not self.agent_loop:
            return json.dumps({"ok": False, "error": "Agent 未初始化"})
        try:
            from PyQt6.QtWidgets import QApplication
            from PyQt6.QtCore import Qt
            import tempfile
            from pathlib import Path

            # 1. 截图
            screen = QApplication.primaryScreen()
            if not screen:
                return json.dumps({"ok": False, "error": "无法获取屏幕"})
            pixmap = screen.grabWindow(0)
            tmp_dir = Path(tempfile.gettempdir()) / "xagent_screenshots"
            tmp_dir.mkdir(exist_ok=True)
            img_path = tmp_dir / f"screen_{int(__import__('time').time())}.png"
            pixmap.save(str(img_path))

            # 2. 视觉感知（如果可用）
            perception = None
            if hasattr(self.agent_loop, "perceive_ui"):
                perception = self.agent_loop.perceive_ui(target=str(img_path))

            if perception:
                ctx = perception.to_context_string(max_elements=20)
                return json.dumps({"ok": True, "context": ctx, "image_path": str(img_path)})
            else:
                return json.dumps({"ok": False, "error": "视觉感知未启用"})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})

    @pyqtSlot(result=str)
    def getConfig(self) -> str:
        """获取当前配置（脱敏）"""
        if not self.config:
            return json.dumps({})
        return json.dumps({
            "provider": self.config.get("model.provider"),
            "model_id": self.config.get("model.model_id"),
            "project_root": str(self.config.project_root),
        })

    @pyqtSlot(str, result=str)
    def confirmDangerous(self, command_json: str) -> str:
        """危险操作确认（前端弹窗后回调）"""
        try:
            data = json.loads(command_json)
            # 这里只是记录，实际确认逻辑在主窗口处理
            return json.dumps({"ok": True, "approved": True})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})

    @pyqtSlot(result=str)
    def getSwarmStatus(self) -> str:
        """获取 Swarm 状态（Worker、Checkpoint、统计）"""
        try:
            from pathlib import Path
            from xagent.core.swarm.checkpoint import CheckpointStore

            swarm_cfg = self.config._data.get("swarm", {}) if self.config else {}
            cp_dir = Path(swarm_cfg.get("checkpoint", {}).get("dir", str(Path.home() / ".xagent" / "swarm_checkpoints")))
            store = CheckpointStore(cp_dir)
            checkpoints = store.list_all()

            # 统计
            pending = sum(1 for cp in checkpoints if cp.status == "pending")
            running = sum(1 for cp in checkpoints if cp.status == "running")
            completed = sum(1 for cp in checkpoints if cp.status == "completed")
            failed = sum(1 for cp in checkpoints if cp.status == "failed")

            return json.dumps({
                "ok": True,
                "enabled": swarm_cfg.get("enabled", False),
                "workers": swarm_cfg.get("workers", 2),
                "checkpoint_dir": str(cp_dir),
                "stats": {
                    "pending": pending,
                    "running": running,
                    "completed": completed,
                    "failed": failed,
                    "total": len(checkpoints),
                },
                "recent": [
                    {
                        "checkpoint_id": cp.checkpoint_id,
                        "node_id": cp.node_id,
                        "status": cp.status,
                        "retry_count": cp.retry_count,
                    }
                    for cp in checkpoints[:10]
                ],
            })
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})
