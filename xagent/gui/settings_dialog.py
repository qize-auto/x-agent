"""
设置对话框
==========
左侧分类导航 + 右侧详细设置面板。
支持模型、缓存、成本、记忆、安全、界面、路由、关于等配置。
"""
from __future__ import annotations
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QTextEdit,
    QPushButton, QListWidget, QListWidgetItem, QStackedWidget,
    QGroupBox, QFormLayout, QMessageBox, QSlider, QSizePolicy,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont


class SettingsDialog(QDialog):
    """
    X-Agent 设置对话框

    左侧: 分类导航列表
    右侧: 对应分类的设置面板
    """

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("⚙️ X-Agent 设置")
        self.setMinimumSize(720, 520)
        self._setup_ui()
        self._load_config()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 左侧导航 ──
        self.nav = QListWidget(self)
        self.nav.setMaximumWidth(160)
        self.nav.setMinimumWidth(140)
        self.nav.setSpacing(4)
        self.nav.setFrameShape(QListWidget.Shape.NoFrame)
        self.nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.nav.setStyleSheet("""
            QListWidget {
                background: #f5f5f5;
                border-right: 1px solid #ddd;
                padding: 8px 4px;
            }
            QListWidget::item {
                padding: 10px 12px;
                border-radius: 6px;
                color: #555;
            }
            QListWidget::item:selected {
                background: #007aff;
                color: white;
            }
            QListWidget::item:hover:!selected {
                background: #e8e8e8;
            }
        """)

        categories = [
            ("🤖 模型", "model"),
            ("💾 缓存", "cache"),
            ("💰 成本", "cost"),
            ("🧠 记忆", "memory"),
            ("👁️ 视觉", "vision"),
            ("🧠 代码", "code_intel"),
            ("🔒 安全", "safety"),
            ("🗣️ 澄清", "clarification"),
            ("🎨 界面", "appearance"),
            ("🎯 路由", "routing"),
            ("ℹ️ 关于", "about"),
        ]
        for label, key in categories:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(QSize(140, 40))
            self.nav.addItem(item)

        self.nav.currentRowChanged.connect(self._on_nav_changed)
        layout.addWidget(self.nav)

        # ── 右侧内容区 ──
        self.stack = QStackedWidget(self)
        self._build_panels()
        layout.addWidget(self.stack, 1)

        # ── 底部按钮 ──
        btn_bar = QWidget(self)
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(16, 8, 16, 12)

        self.btn_save = QPushButton("💾 保存", self)
        self.btn_save.setStyleSheet("""
            QPushButton {
                background: #007aff; color: white; padding: 8px 24px;
                border-radius: 6px; font-weight: bold;
            }
            QPushButton:hover { background: #0051d5; }
        """)
        self.btn_save.clicked.connect(self._on_save)

        self.btn_cancel = QPushButton("取消", self)
        self.btn_cancel.setStyleSheet("""
            QPushButton { padding: 8px 20px; border-radius: 6px; }
        """)
        self.btn_cancel.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_save)

        # 把按钮放到右侧区域底部
        right_widget = QWidget(self)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.stack, 1)
        right_layout.addWidget(btn_bar)
        layout.addWidget(right_widget, 1)

        # 默认选中第一项
        self.nav.setCurrentRow(0)

    def _build_panels(self):
        """构建所有设置面板"""
        self.panels = {}

        # 1. 模型
        self.panels["model"] = self._build_model_panel()
        # 2. 缓存
        self.panels["cache"] = self._build_cache_panel()
        # 3. 成本
        self.panels["cost"] = self._build_cost_panel()
        # 4. 记忆
        self.panels["memory"] = self._build_memory_panel()
        # 5. 视觉
        self.panels["vision"] = self._build_vision_panel()
        # 6. 代码智能
        self.panels["code_intel"] = self._build_code_intel_panel()
        # 7. 安全
        self.panels["safety"] = self._build_safety_panel()
        # 8. 澄清
        self.panels["clarification"] = self._build_clarification_panel()
        # 9. 界面
        self.panels["appearance"] = self._build_appearance_panel()
        # 10. 路由
        self.panels["routing"] = self._build_routing_panel()
        # 11. 关于
        self.panels["about"] = self._build_about_panel()

        for key, panel in self.panels.items():
            self.stack.addWidget(panel)

    # ───────────────────────── 各面板构建 ─────────────────────────

    def _build_model_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        group = QGroupBox("模型连接")
        form = QFormLayout(group)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.in_provider = QComboBox()
        self.in_provider.addItems([
            "openrouter", "openai", "anthropic", "moonshot", "deepseek", "ollama"
        ])
        self.in_provider.setStyleSheet("padding: 4px 8px;")
        form.addRow("Provider:", self.in_provider)

        self.in_model_id = QLineEdit()
        self.in_model_id.setPlaceholderText("如: deepseek/deepseek-chat")
        form.addRow("模型 ID:", self.in_model_id)

        self.in_api_key = QLineEdit()
        self.in_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.in_api_key.setPlaceholderText("sk-...")
        form.addRow("API Key:", self.in_api_key)

        self.in_base_url = QLineEdit()
        self.in_base_url.setPlaceholderText("留空使用默认")
        form.addRow("Base URL:", self.in_base_url)

        layout.addWidget(group)

        # ── 高级参数 ──
        adv_group = QGroupBox("🔧 高级参数")
        adv_form = QFormLayout(adv_group)
        adv_form.setSpacing(10)

        self.in_temperature = QDoubleSpinBox()
        self.in_temperature.setRange(0.0, 2.0)
        self.in_temperature.setSingleStep(0.1)
        self.in_temperature.setDecimals(1)
        adv_form.addRow("Temperature:", self.in_temperature)

        self.in_max_tokens = QSpinBox()
        self.in_max_tokens.setRange(256, 128000)
        self.in_max_tokens.setSingleStep(1024)
        adv_form.addRow("Max Tokens:", self.in_max_tokens)

        layout.addWidget(adv_group)

        # 预设管理
        preset_group = QGroupBox("模型预设")
        preset_layout = QVBoxLayout(preset_group)
        self.preset_list = QListWidget()
        self.preset_list.setMaximumHeight(120)
        preset_layout.addWidget(self.preset_list)
        layout.addWidget(preset_group)

        layout.addStretch()
        return w

    def _build_cache_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        group = QGroupBox("缓存策略")
        form = QFormLayout(group)
        form.setSpacing(12)

        self.in_cache_mode = QComboBox()
        self.in_cache_mode.addItems(["auto", "always", "never"])
        self.in_cache_mode.setToolTip(
            "auto: DeepSeek 自动启用缓存\n"
            "always: 所有 Provider 启用缓存结构\n"
            "never: 禁用，使用原有行为"
        )
        form.addRow("Cache Mode:", self.in_cache_mode)

        self.in_session_persist = QCheckBox("跨对话保留上下文（Session 持久化）")
        self.in_session_persist.setToolTip(
            "开启后，连续对话会累积上下文，提高缓存命中率"
        )
        form.addRow(self.in_session_persist)

        layout.addWidget(group)
        layout.addStretch()
        return w

    def _build_cost_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        group = QGroupBox("成本控制")
        form = QFormLayout(group)
        form.setSpacing(12)

        self.in_cost_preset = QComboBox()
        self.in_cost_preset.addItems(["flash", "auto", "pro"])
        self.in_cost_preset.setToolTip(
            "flash: 始终用最便宜模型\n"
            "auto: 默认便宜，困难时自动升级\n"
            "pro: 始终用最贵模型"
        )
        form.addRow("默认 Preset:", self.in_cost_preset)

        self.in_auto_escalate = QCheckBox("失败信号自动升级")
        self.in_auto_escalate.setToolTip("工具调用多次失败后自动切换到 pro 模型")
        form.addRow(self.in_auto_escalate)

        self.in_compaction = QCheckBox("启用上下文压缩")
        self.in_compaction.setToolTip("长工具结果自动压缩为摘要，减少 token 消耗")
        form.addRow(self.in_compaction)

        layout.addWidget(group)
        layout.addStretch()
        return w

    def _build_memory_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        group = QGroupBox("记忆引擎")
        form = QFormLayout(group)
        form.setSpacing(12)

        self.in_memory_enabled = QCheckBox("启用记忆")
        form.addRow(self.in_memory_enabled)

        self.in_memory_dir = QLineEdit()
        self.in_memory_dir.setPlaceholderText("记忆持久化目录")
        form.addRow("存储目录:", self.in_memory_dir)

        layout.addWidget(group)
        layout.addStretch()
        return w

    def _build_safety_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        group = QGroupBox("安全策略")
        form = QFormLayout(group)
        form.setSpacing(12)

        self.in_safety_mode = QComboBox()
        self.in_safety_mode.addItems(["auto", "semi", "manual"])
        self.in_safety_mode.setToolTip(
            "auto: 自动阻止危险命令\n"
            "semi: 需要确认（默认）\n"
            "manual: 完全手动控制"
        )
        form.addRow("安全模式:", self.in_safety_mode)

        self.in_dangerous_cmds = QTextEdit()
        self.in_dangerous_cmds.setMaximumHeight(100)
        self.in_dangerous_cmds.setPlaceholderText("每行一个命令，如: rm\nrmdir\nformat")
        form.addRow("危险命令列表:", self.in_dangerous_cmds)

        # ── 自我改进安全开关 ──
        si_group = QGroupBox("🔄 自我改进（全自动）")
        si_form = QFormLayout(si_group)
        si_form.setSpacing(10)

        self.in_si_enabled = QCheckBox("启用自我改进")
        self.in_si_enabled.setToolTip(
            "Agent 自动记录失败经验并尝试优化系统提示词（全自动，无需手动操作）"
        )
        si_form.addRow(self.in_si_enabled)

        self.in_si_auto_apply = QCheckBox("自动应用优化结果")
        self.in_si_auto_apply.setToolTip(
            "ShadowEval 通过后自动 rollout 新 prompt；关闭则仅记录不应用"
        )
        si_form.addRow(self.in_si_auto_apply)

        layout.addWidget(si_group)
        layout.addWidget(group)
        layout.addStretch()
        return w

    def _build_clarification_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        group = QGroupBox("需求澄清 (Requirement Clarification)")
        form = QFormLayout(group)
        form.setSpacing(12)

        self.in_clarification_enabled = QCheckBox("启用任务执行前的需求澄清")
        self.in_clarification_enabled.setToolTip(
            "开启后，执行复杂任务前会先与您确认需求规格，避免返工"
        )
        form.addRow(self.in_clarification_enabled)

        self.in_clarification_auto_skip = QCheckBox("简单任务自动跳过澄清")
        self.in_clarification_auto_skip.setToolTip(
            "如 ls、git status 等简单命令不触发澄清"
        )
        form.addRow(self.in_clarification_auto_skip)

        self.in_clarification_cheap = QCheckBox("澄清阶段使用 Cheap Model")
        self.in_clarification_cheap.setToolTip(
            "降低澄清阶段的 API 成本"
        )
        form.addRow(self.in_clarification_cheap)

        layout.addWidget(group)
        layout.addStretch()
        return w

    def _build_vision_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        group = QGroupBox("视觉感知")
        form = QFormLayout(group)
        form.setSpacing(12)

        self.in_vision_enabled = QCheckBox("启用视觉感知")
        self.in_vision_enabled.setToolTip("启用后 Agent 可分析屏幕截图、浏览器页面等视觉内容")
        form.addRow(self.in_vision_enabled)

        self.in_vision_screenshot_dir = QLineEdit()
        self.in_vision_screenshot_dir.setPlaceholderText(str(Path.home() / ".xagent" / "screenshots"))
        form.addRow("截图目录:", self.in_vision_screenshot_dir)

        self.in_vision_ocr_lang = QLineEdit()
        self.in_vision_ocr_lang.setPlaceholderText("chi_sim+eng")
        form.addRow("OCR 语言:", self.in_vision_ocr_lang)

        layout.addWidget(group)
        layout.addStretch()
        return w

    def _build_code_intel_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        group = QGroupBox("代码智能")
        form = QFormLayout(group)
        form.setSpacing(12)

        self.in_codeintel_enabled = QCheckBox("启用代码智能")
        self.in_codeintel_enabled.setToolTip("启用后 Agent 可分析代码结构、符号依赖、执行语义编辑")
        form.addRow(self.in_codeintel_enabled)

        self.in_codeintel_exclude = QTextEdit()
        self.in_codeintel_exclude.setMaximumHeight(80)
        self.in_codeintel_exclude.setPlaceholderText("每行一个排除模式，如:\nnode_modules/\n__pycache__/")
        form.addRow("排除模式:", self.in_codeintel_exclude)

        layout.addWidget(group)
        layout.addStretch()
        return w

    def _build_appearance_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        group = QGroupBox("界面偏好")
        form = QFormLayout(group)
        form.setSpacing(12)

        self.in_theme = QComboBox()
        self.in_theme.addItems(["dark", "light", "system"])
        form.addRow("主题:", self.in_theme)

        self.in_iframe_url = QLineEdit()
        self.in_iframe_url.setPlaceholderText("https://kimi.moonshot.cn")
        form.addRow("侧边 iframe URL:", self.in_iframe_url)

        self.in_auto_start = QCheckBox("启动时自动连接")
        form.addRow(self.in_auto_start)

        layout.addWidget(group)
        layout.addStretch()
        return w

    def _build_routing_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        group = QGroupBox("智能路由")
        form = QFormLayout(group)
        form.setSpacing(12)

        self.in_routing_enabled = QCheckBox("启用智能路由")
        form.addRow(self.in_routing_enabled)

        self.in_routing_strategy = QComboBox()
        self.in_routing_strategy.addItems(["cost_first", "quality_first", "balanced"])
        form.addRow("默认策略:", self.in_routing_strategy)

        layout.addWidget(group)
        layout.addStretch()
        return w

    def _build_about_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("X-Agent")
        title_font = QFont()
        title_font.setPointSize(24)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        version = QLabel("版本: 0.1.0")
        layout.addWidget(version)

        info = QLabel(
            "X-Agent 是一个多模型 AI Agent 框架，支持代码编辑、"
            "文件操作、Shell 命令、网页搜索和任务规划。\n\n"
            "配置存储: ~/.xagent/config.json\n"
            "记忆存储: ~/.xagent/memory/\n"
            "项目地址: ~/kimi-workspace/x-agent"
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #666; line-height: 1.6;")
        layout.addWidget(info)

        layout.addStretch()
        return w

    # ───────────────────────── 事件处理 ─────────────────────────

    def _on_nav_changed(self, row: int):
        item = self.nav.item(row)
        if item:
            key = item.data(Qt.ItemDataRole.UserRole)
            self.stack.setCurrentWidget(self.panels[key])

    def _load_config(self):
        """从 config 加载值到 UI"""
        d = self.config._data if self.config else {}

        # 模型
        model = d.get("model", {})
        self.in_provider.setCurrentText(model.get("provider", "openrouter"))
        self.in_model_id.setText(model.get("model_id", ""))
        self.in_api_key.setText(model.get("api_key", ""))
        self.in_base_url.setText(model.get("base_url", ""))
        self.in_temperature.setValue(model.get("temperature", 0.7))
        self.in_max_tokens.setValue(model.get("max_tokens", 4096))

        # 预设列表
        presets = d.get("model_presets", {})
        self.preset_list.clear()
        for name, info in presets.items():
            model_str = info.get("model_id", "?")
            self.preset_list.addItem(f"{name} → {model_str}")

        # 缓存
        cache = d.get("cache", {})
        self.in_cache_mode.setCurrentText(cache.get("mode", "auto"))
        self.in_session_persist.setChecked(cache.get("session_persist", False))

        # 成本
        cost = d.get("cost_control", {})
        self.in_cost_preset.setCurrentText(cost.get("preset", "flash"))
        self.in_auto_escalate.setChecked(cost.get("auto_escalation", True))
        self.in_compaction.setChecked(cost.get("turn_end_compaction", True))

        # 记忆
        memory = d.get("memory", {})
        self.in_memory_enabled.setChecked(memory.get("enabled", True))
        self.in_memory_dir.setText(memory.get("persist_dir", ""))

        # 安全
        safety = d.get("safety", {})
        self.in_safety_mode.setCurrentText(safety.get("mode", "semi"))
        cmds = safety.get("dangerous_commands", [])
        self.in_dangerous_cmds.setPlainText("\n".join(cmds))

        # 澄清
        clar = d.get("clarification", {})
        self.in_clarification_enabled.setChecked(clar.get("enabled", False))
        self.in_clarification_auto_skip.setChecked(clar.get("auto_skip_simple", True))
        self.in_clarification_cheap.setChecked(clar.get("use_cheap_model", True))

        # 视觉
        vision = d.get("vision", {})
        self.in_vision_enabled.setChecked(vision.get("enabled", True))
        self.in_vision_screenshot_dir.setText(vision.get("screenshot_dir", ""))
        self.in_vision_ocr_lang.setText(vision.get("ocr_language", "chi_sim+eng"))

        # 代码智能
        ci = d.get("code_intel", {})
        self.in_codeintel_enabled.setChecked(ci.get("enabled", True))
        self.in_codeintel_exclude.setPlainText("\n".join(ci.get("exclude_patterns", [])))

        # 安全面板中的自我改进开关
        si = d.get("self_improve", {})
        self.in_si_enabled.setChecked(si.get("enabled", False))
        self.in_si_auto_apply.setChecked(si.get("auto_apply", False))

        # 界面
        gui = d.get("gui", {})
        self.in_theme.setCurrentText(gui.get("theme", "dark"))
        self.in_iframe_url.setText(gui.get("iframe_url", ""))
        self.in_auto_start.setChecked(gui.get("auto_start", True))

        # 路由
        routing = d.get("routing", {})
        self.in_routing_enabled.setChecked(routing.get("enabled", True))
        self.in_routing_strategy.setCurrentText(routing.get("default_strategy", "balanced"))

    def _on_save(self):
        """保存 UI 值到 config"""
        d = self.config._data if self.config else {}

        # 模型
        d.setdefault("model", {})
        d["model"]["provider"] = self.in_provider.currentText()
        d["model"]["model_id"] = self.in_model_id.text().strip()
        d["model"]["api_key"] = self.in_api_key.text().strip()
        d["model"]["base_url"] = self.in_base_url.text().strip()
        d["model"]["temperature"] = self.in_temperature.value()
        d["model"]["max_tokens"] = self.in_max_tokens.value()

        # 缓存（thought_harvest/warmup 为全自动，不通过 GUI 覆盖）
        d.setdefault("cache", {})
        d["cache"]["mode"] = self.in_cache_mode.currentText()
        d["cache"]["session_persist"] = self.in_session_persist.isChecked()

        # 成本（thresholds 保留配置文件原有值）
        d.setdefault("cost_control", {})
        d["cost_control"]["preset"] = self.in_cost_preset.currentText()
        d["cost_control"]["auto_escalation"] = self.in_auto_escalate.isChecked()
        d["cost_control"]["turn_end_compaction"] = self.in_compaction.isChecked()

        # 记忆（embedding/max_history 保留配置文件原有值）
        d.setdefault("memory", {})
        d["memory"]["enabled"] = self.in_memory_enabled.isChecked()
        d["memory"]["persist_dir"] = self.in_memory_dir.text().strip()

        # 安全
        d.setdefault("safety", {})
        d["safety"]["mode"] = self.in_safety_mode.currentText()
        raw_cmds = self.in_dangerous_cmds.toPlainText().strip()
        d["safety"]["dangerous_commands"] = [c.strip() for c in raw_cmds.split("\n") if c.strip()]

        # 视觉（strategy/code_fusion 保留配置文件原有值）
        d.setdefault("vision", {})
        d["vision"]["enabled"] = self.in_vision_enabled.isChecked()
        d["vision"]["screenshot_dir"] = self.in_vision_screenshot_dir.text().strip()
        d["vision"]["ocr_language"] = self.in_vision_ocr_lang.text().strip()

        # 代码智能（index_strategy/repo_map/semantic 保留配置文件原有值）
        d.setdefault("code_intel", {})
        d["code_intel"]["enabled"] = self.in_codeintel_enabled.isChecked()
        raw_exclude = self.in_codeintel_exclude.toPlainText().strip()
        d["code_intel"]["exclude_patterns"] = [e.strip() for e in raw_exclude.split("\n") if e.strip()]

        # 自我改进（仅保存 GUI 中可见的开关；threshold/min_score 保留原有值）
        d.setdefault("self_improve", {})
        d["self_improve"]["enabled"] = self.in_si_enabled.isChecked()
        d["self_improve"]["auto_apply"] = self.in_si_auto_apply.isChecked()

        # 界面
        d.setdefault("gui", {})
        d["gui"]["theme"] = self.in_theme.currentText()
        d["gui"]["iframe_url"] = self.in_iframe_url.text().strip()
        d["gui"]["auto_start"] = self.in_auto_start.isChecked()

        # 澄清（mode/max_q/cheap_model_id 保留配置文件原有值）
        d.setdefault("clarification", {})
        d["clarification"]["enabled"] = self.in_clarification_enabled.isChecked()
        d["clarification"]["auto_skip_simple"] = self.in_clarification_auto_skip.isChecked()
        d["clarification"]["use_cheap_model"] = self.in_clarification_cheap.isChecked()

        # 路由（budget 保留配置文件原有值）
        d.setdefault("routing", {})
        d["routing"]["enabled"] = self.in_routing_enabled.isChecked()
        d["routing"]["default_strategy"] = self.in_routing_strategy.currentText()

        self.config.save()
        QMessageBox.information(self, "保存成功", "配置已保存到 ~/.xagent/config.json\n重启后完全生效。")
        self.accept()
