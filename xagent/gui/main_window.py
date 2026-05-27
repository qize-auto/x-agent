"""
X-Agent 主窗口
=============
基于 PySide6 + QWebEngineView + iframe 架构（复用 Kimi-X 模板）。
右侧面板改为 Agent 控制台，支持对话、状态显示、工具调用记录。
"""
from __future__ import annotations
import sys, json, os, time
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSystemTrayIcon, QMenu, QApplication, QMessageBox,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtCore import QUrl, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon, QAction

from .bridge import AgentBridge
from .worker import AgentWorker
from .settings_dialog import SettingsDialog


class AgentMainWindow(QMainWindow):
    agentMessage = pyqtSignal(str, object)
    agentError = pyqtSignal(str)

    def __init__(self, config=None, agent_loop=None):
        super().__init__()
        self.config = config
        self.agent_loop = agent_loop
        self._worker: AgentWorker | None = None
        self._setup_ui()
        self._setup_tray()
        self._setup_signals()
        # 延迟展示启动时的未确认系统错误（确保面板已加载）
        QTimer.singleShot(800, self._show_startup_errors)

    # ── UI ───────────────────────────────────────────────
    def _setup_ui(self):
        self.setWindowTitle("X-Agent")
        if self.config:
            geo = self.config.ui.get("geometry", [100, 80, 1400, 880])
            self.setGeometry(*geo)
        else:
            self.setGeometry(100, 80, 1400, 880)

        cw = QWidget(self)
        self.setCentralWidget(cw)
        layout = QHBoxLayout(cw)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # iframe 区域: 左侧 65%
        self.web_view = QWebEngineView(self)
        self.web_view.setMinimumWidth(500)
        layout.addWidget(self.web_view, 65)

        # Agent 面板: 右侧 35%
        self.panel_view = QWebEngineView(self)
        self.panel_view.setMinimumWidth(380)
        layout.addWidget(self.panel_view, 35)

        # 加载面板 HTML
        panel_html = Path(__file__).parent.parent / "web_ui" / "index.html"
        if panel_html.exists():
            self.panel_view.load(QUrl.fromLocalFile(str(panel_html.resolve())))

        # WebChannel
        self.channel = QWebChannel(self)
        self.bridge = AgentBridge(parent=self, agent_loop=self.agent_loop, config=self.config)
        self.channel.registerObject("agentBridge", self.bridge)
        self.panel_view.page().setWebChannel(self.channel)

        # 加载 iframe URL（默认 kimi web）
        self._load_iframe()

        # 菜单
        self._build_menu()

    def _load_iframe(self):
        if self.config and self.config.ui.get("iframe_url"):
            url = self.config.ui["iframe_url"]
        else:
            url = "https://kimi.moonshot.cn"
        self.web_view.load(QUrl(url))

    def _build_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件")
        act_exit = QAction("退出", self)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        model_menu = menubar.addMenu("模型")
        if self.config:
            for preset in self.config.list_model_presets():
                act = QAction(preset, self)
                act.triggered.connect(lambda checked, p=preset: self._switch_model(p))
                model_menu.addAction(act)

        view_menu = menubar.addMenu("视图")
        act_reload = QAction("刷新面板", self)
        act_reload.triggered.connect(self._reload_panel)
        view_menu.addAction(act_reload)

        # 👁 视觉感知
        vision_menu = menubar.addMenu("👁 视觉")
        act_perceive = QAction("感知当前屏幕", self)
        act_perceive.triggered.connect(self._perceive_screen)
        vision_menu.addAction(act_perceive)

        # ⚙️ 设置按钮（齿轮图标或文字）
        settings_menu = menubar.addMenu("⚙️ 设置")
        act_settings = QAction("打开设置…", self)
        act_settings.triggered.connect(self._open_settings)
        settings_menu.addAction(act_settings)

    # ── 托盘 ─────────────────────────────────────────────
    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        # 使用系统默认图标（无自定义图标时）
        self.tray.setVisible(True)
        menu = QMenu(self)
        act_show = QAction("显示", self)
        act_show.triggered.connect(self.showNormal)
        menu.addAction(act_show)
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(QApplication.instance().quit)
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.showNormal()
            self.raise_()

    # ── 信号 ─────────────────────────────────────────────
    def _setup_signals(self):
        self.agentMessage.connect(self._on_agent_message)
        self.agentError.connect(self._on_agent_error)

    # ── 公开方法（供前端/外部调用）─────────────────────────
    def _run_agent(self, user_input: str, mode: str = "chat"):
        """启动后台 Agent 线程"""
        if self._worker and self._worker.isRunning():
            self._push_panel_js('showToast("Agent 正在处理中…")')
            return
        status_label = {"plan": "任务规划中…", "execute": "执行中…", "task": "任务规划中…"}.get(mode, "思考中…")
        self._worker = AgentWorker(self.agent_loop, user_input, mode=mode)
        self._worker.messageEmitted.connect(self._on_worker_message)
        self._worker.turnFinished.connect(self._on_worker_finished)
        self._worker.errorOccurred.connect(self._on_worker_error)
        self._worker.start()
        self._push_panel_js(f'setStatus("thinking", "{status_label}")')

    def _execute_plan(self, plan_json: str):
        """前端确认计划后，执行计划"""
        self._run_agent(plan_json, mode="execute")

    def _switch_model(self, preset: str):
        if self.config:
            self.config.set_model_preset(preset)
            self._push_panel_js(f'showToast("已切换模型: {preset}")')

    def _open_settings(self):
        """打开设置对话框"""
        dlg = SettingsDialog(config=self.config, parent=self)
        if dlg.exec() == SettingsDialog.DialogCode.Accepted:
            self._push_panel_js('showToast("配置已保存，部分设置需重启生效")')

    def _reload_panel(self):
        panel_html = Path(__file__).parent.parent / "web_ui" / "index.html"
        if panel_html.exists():
            self.panel_view.load(QUrl.fromLocalFile(str(panel_html.resolve())))

    def _perceive_screen(self):
        """菜单触发：感知屏幕并将结果插入对话"""
        if not self.agent_loop:
            self._push_panel_js('showToast("Agent 未初始化")')
            return
        try:
            self._push_panel_js('setStatus("thinking", "感知屏幕中…")')
            perception = self.agent_loop.perceive_ui(target="screen")
            if perception:
                ctx = perception.to_context_string(max_elements=15)
                # 将感知结果作为用户消息插入对话
                self._push_panel_js(f'appendVisionContext({json.dumps(ctx)})')
                self._push_panel_js('setStatus("idle", "就绪")')
            else:
                self._push_panel_js('showToast("视觉感知未启用或不可用")')
                self._push_panel_js('setStatus("idle", "就绪")')
        except Exception as e:
            self._push_panel_js(f'showToast("感知失败: {str(e)}")')
            self._push_panel_js('setStatus("error", "出错")')

    # ── JS 通信 helpers ──────────────────────────────────
    def _push_panel_js(self, js: str):
        """在面板页执行 JS"""
        self.panel_view.page().runJavaScript(js)

    def _on_worker_message(self, msg_type: str, payload: object):
        """中间状态: tool_call / tool_result / thinking"""
        if msg_type == "tool_call":
            name = payload.get("name", "?")
            self._push_panel_js(f'appendToolCall({json.dumps(name)})')
        elif msg_type == "tool_result":
            self._push_panel_js(f'setStatus("idle", "就绪")')
        elif msg_type == "thinking":
            self._push_panel_js('setStatus("thinking", "思考中…")')

    def _on_worker_finished(self, result):
        """一轮结束，推送答案到前端"""
        self._push_panel_js('setStatus("idle", "就绪")')
        if hasattr(result, "to_markdown"):
            # TaskPlan 对象
            if getattr(result, "status", "") == "planning":
                # 刚生成计划，需要用户确认
                import json
                plan_data = {
                    "goal": result.goal,
                    "subtasks": [st.to_dict() for st in result.subtasks],
                }
                self._push_panel_js(f'showPlanConfirm({json.dumps(plan_data)})')
            else:
                plan_md = result.to_markdown()
                self._push_panel_js(f'appendAgentMessage({json.dumps(plan_md)})')
        else:
            # 普通字符串回复
            self._push_panel_js(f'appendAgentMessage({json.dumps(str(result))})')
        self._worker = None

    def _on_worker_error(self, error: str):
        # 兜底记录到 ErrorLedger（worker 中可能已记录，这里不重复 thanks to 指纹去重）
        try:
            from xagent.core.error_ledger import ErrorLedger
            ledger = ErrorLedger()
            ledger.record(
                category="runtime",
                message="Agent 执行异常",
                detail=error[:300],
            )
        except Exception:
            pass
        self._push_panel_js('setStatus("error", "出错")')
        self._push_panel_js(f'appendError({json.dumps(error)})')
        # 简洁 toast：只取第一行，限制 80 字符
        short = error.splitlines()[0][:80] + "..." if len(error) > 80 else error
        self._push_panel_js(f'showToast({json.dumps("⚠️ " + short)})')
        self._worker = None

    def _on_agent_message(self, msg_type: str, payload: object):
        self._on_worker_message(msg_type, payload)

    def _on_agent_error(self, error: str):
        self._on_worker_error(error)

    def _show_startup_errors(self):
        """展示启动时的未确认系统错误（已确认的不再重复提醒）"""
        try:
            from xagent.core.error_ledger import ErrorLedger
            ledger = ErrorLedger()
            errors = ledger.get_unacknowledged(
                categories=["config_validation", "import_failure"],
                max_age_sec=604800,
            )
            for err in errors[:3]:  # 最多同时展示 3 条，避免信息轰炸
                msg = f"⚠️ {err.message}"
                if err.detail:
                    short = err.detail[:120] + "..." if len(err.detail) > 120 else err.detail
                    msg += f" | {short}"
                self._push_panel_js(f'showToast({json.dumps(msg)})')
                ledger.acknowledge(err.fingerprint)
        except Exception:
            pass

    # ── 窗口事件 ─────────────────────────────────────────
    def closeEvent(self, event):
        if self.config:
            self.config.ui["geometry"] = [
                self.x(), self.y(), self.width(), self.height()
            ]
            self.config.save()
        event.accept()
