#!/usr/bin/env python3
"""
X-Agent GUI 入口
===============
双击运行或: python main.py
"""
from __future__ import annotations
import sys


def check_dependencies():
    missing = []
    try:
        import PyQt6
    except ImportError:
        missing.append("PyQt6")
    try:
        import openai
    except ImportError:
        missing.append("openai")
    if missing:
        print("缺少依赖，请安装:")
        print(f"  pip install {' '.join(missing)}")
        sys.exit(1)


def main():
    check_dependencies()

    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import Qt
    from PyQt6.QtWebEngineCore import QWebEnginePage

    from xagent.config import XAgentConfig
    from xagent.core.llm_client import LLMClient
    from xagent.core.tool_registry import ToolRegistry
    from xagent.core.memory_engine import MemoryEngine
    from xagent.core.agent_loop import AgentLoop
    from xagent.tools import register_all_tools
    from xagent.gui.main_window import AgentMainWindow

    # 初始化配置
    config = XAgentConfig()

    # 初始化 LLM
    try:
        llm = LLMClient.from_config(config.model)
    except Exception as e:
        print(f"LLM 初始化失败: {e}")
        print("请检查 ~/.xagent/config.json 中的 API key 和模型配置")
        sys.exit(1)

    # 初始化工具
    tools = ToolRegistry()
    register_all_tools(tools, project_root=str(config.project_root))

    # 初始化记忆
    memory = MemoryEngine(config.memory.get("persist_dir"))

    # 初始化 Agent 循环（先不设置 confirm_callback，等 window 创建后再绑定）
    agent_loop = AgentLoop(
        llm=llm,
        tools=tools,
        memory=memory,
        project_root=str(config.project_root),
        confirm_callback=None,
        router_config=config._data.get("routing"),
    )

    # 启动 GUI
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setApplicationName("X-Agent")
    app.setApplicationVersion("0.1.0")

    window = AgentMainWindow(config=config, agent_loop=agent_loop)
    window.show()

    # 确认回调（线程安全 GUI 弹窗），在 window 实例化后绑定
    from PyQt6.QtCore import QTimer, QThread
    from PyQt6.QtWidgets import QMessageBox, QApplication
    import queue

    _confirm_queue = queue.Queue()

    def _ask_confirm(tool_name, args):
        ret = QMessageBox.question(
            window,
            "确认危险操作",
            f"工具: {tool_name}\n参数: {args}\n\n是否执行？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        _confirm_queue.put(ret == QMessageBox.StandardButton.Yes)

    def confirm_dangerous(tool_name, args):
        if QThread.currentThread() == QApplication.instance().thread():
            _ask_confirm(tool_name, args)
        else:
            QTimer.singleShot(0, lambda: _ask_confirm(tool_name, args))
        return _confirm_queue.get(timeout=300)

    agent_loop.confirm_callback = confirm_dangerous

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
