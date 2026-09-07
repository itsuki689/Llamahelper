"""主窗口：侧边导航 + 页面栈 + 各模块信号接线。"""
from __future__ import annotations

import time
import urllib.error
import urllib.request

from PySide6.QtCore import QEvent, Qt, QThread, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from benchmark.runner import BenchJob, BenchmarkRunner
from config.app_config import AppConfig
from config.presets import PresetManager
from core.params import ServerParams
from core.perf_parser import parse_perf_line
from core.server_manager import STATE_RUNNING, STATE_STARTING, STATE_STOPPED, ServerManager
from monitoring.gpu_monitor import GpuMonitor
from monitoring.system_monitor import SystemMonitor
from ui.pages.benchmark_page import BenchmarkPage
from ui.pages.dashboard import DashboardPage
from ui.pages.logs_page import LogsPage
from ui.pages.parameters import ParametersPage
from ui.pages.presets_page import PresetsPage
from ui.pages.translation_widget_page import TranslationWidgetPage


class ApiHealthChecker(QThread):
    """后台轮询 /health，确认 llama-server API 就绪，不阻塞 UI。"""

    ready = Signal()
    failed = Signal(str)

    def __init__(self, host: str, port: int, timeout: float = 300.0,
                 parent=None) -> None:
        super().__init__(parent)
        self._url = ServerParams(host=host, port=port).api_base_url() + "/health"
        self._deadline = timeout
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        end = time.monotonic() + self._deadline
        while self._running and time.monotonic() < end:
            try:
                with urllib.request.urlopen(self._url, timeout=2) as resp:
                    if resp.status == 200:
                        if self._running:
                            self.ready.emit()
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.5)
        if self._running:
            self.failed.emit("等待 API 就绪超时")


class WindowDragLabel(QLabel):
    """无标题栏窗口的原生拖动区域。"""

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            window.showNormal() if window.isMaximized() else window.showMaximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    NAV = [("Dashboard", "仪表盘"), ("Parameters", "参数"),
           ("Benchmark", "性能测试"), ("Logs", "日志"), ("Presets", "预设"),
           ("TranslationWidget", "翻译挂件")]

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("LlamaHelper — llama.cpp 调参与性能测试")
        self.resize(1180, 780)

        # ---- 核心组件 ----
        self.app_config = AppConfig()
        self.preset_manager = PresetManager()
        self.server = ServerManager(self)
        self.health_checker: ApiHealthChecker | None = None
        self.bench_runner: BenchmarkRunner | None = None
        self._translate_widget = None
        self._close_requested = False
        from PySide6.QtCore import QTimer
        self._close_timer = QTimer(self)
        self._close_timer.setInterval(50)
        self._close_timer.timeout.connect(self.close)

        # ---- UI ----
        self._build_ui()
        self._wire_signals()

        # ---- 监控线程 ----
        self.gpu_monitor = GpuMonitor(parent=self)
        self.gpu_monitor.stats.connect(self.dashboard.update_gpu)
        self.gpu_monitor.unavailable.connect(self.dashboard.set_gpu_unavailable)
        self.gpu_monitor.start()
        self.sys_monitor = SystemMonitor(parent=self)
        self.sys_monitor.stats.connect(self.dashboard.update_system)
        self.sys_monitor.start()

        # ---- 恢复上次配置 ----
        self.parameters.set_params(self.app_config.load_params())
        self.dashboard.set_server_state(STATE_STOPPED)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 侧边栏
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(190)
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(0, 0, 0, 12)
        sb.setSpacing(0)

        title = WindowDragLabel("LlamaHelper")
        title.setObjectName("appTitle")
        title.setToolTip("拖动窗口 · 双击最大化/还原")
        subtitle = WindowDragLabel("llama.cpp 调参 · 性能测试")
        subtitle.setObjectName("appSubtitle")
        subtitle.setToolTip("拖动窗口 · 双击最大化/还原")
        sb.addWidget(title)
        sb.addWidget(subtitle)
        self.nav_buttons: list[QPushButton] = []
        self.stack = QStackedWidget()
        for i, (_key, label) in enumerate(self.NAV):
            btn = QPushButton(label)
            btn.setObjectName("navBtn")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, idx=i: self._switch_page(idx))
            sb.addWidget(btn)
            self.nav_buttons.append(btn)
        sb.addStretch(1)

        # 页面
        self.dashboard = DashboardPage()
        self.parameters = ParametersPage()
        self.benchmark = BenchmarkPage()
        self.logs = LogsPage()
        self.presets = PresetsPage(self.preset_manager)
        self.translation_widget_page = TranslationWidgetPage()
        for p in (self.dashboard, self.parameters, self.benchmark,
                  self.logs, self.presets, self.translation_widget_page):
            self.stack.addWidget(p)

        lay.addWidget(sidebar)

        # 右侧保留 Apple 风格主体，同时补回 Windows 常用的标题栏按钮。
        content = QWidget()
        content.setObjectName("contentArea")
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)

        title_bar = QFrame()
        title_bar.setObjectName("windowTitleBar")
        title_bar.setFixedHeight(36)
        title_bar_lay = QHBoxLayout(title_bar)
        title_bar_lay.setContentsMargins(0, 0, 0, 0)
        title_bar_lay.setSpacing(0)
        drag_area = WindowDragLabel("")
        drag_area.setObjectName("windowDragArea")
        drag_area.setToolTip("拖动窗口 · 双击最大化/还原")
        title_bar_lay.addWidget(drag_area, 1)

        self.btn_win_minimize = QPushButton("\u2212")
        self.btn_win_maximize = QPushButton("\u25a1")
        self.btn_win_close = QPushButton("\u00d7")
        for button, role, tip in (
                (self.btn_win_minimize, "minimize", "最小化"),
                (self.btn_win_maximize, "maximize", "最大化/还原"),
                (self.btn_win_close, "close", "关闭")):
            button.setObjectName("winCaptionButton")
            button.setProperty("role", role)
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            button.setFixedSize(46, 35)
            title_bar_lay.addWidget(button)
        self.btn_win_minimize.clicked.connect(self.showMinimized)
        self.btn_win_maximize.clicked.connect(self._toggle_maximized)
        self.btn_win_close.clicked.connect(self.close)

        content_lay.addWidget(title_bar)
        content_lay.addWidget(self.stack, 1)
        lay.addWidget(content, 1)
        self._switch_page(0)

    def _switch_page(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate(self.nav_buttons):
            b.setChecked(i == idx)

    def _toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._sync_maximize_button()

    def _sync_maximize_button(self) -> None:
        if hasattr(self, "btn_win_maximize"):
            self.btn_win_maximize.setText("\u2750" if self.isMaximized() else "\u25a1")

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_maximize_button()

    def open_translate_widget(self) -> None:
        """启动/唤起翻译挂件，跟随当前参数页的 host/port。"""
        from widget.dockable_translate_widget import TranslateWidget

        p = self.parameters.get_params()
        url = p.api_base_url() + "/v1"
        if self._translate_widget is None:
            self._translate_widget = TranslateWidget(url)
            self._translate_widget.closed.connect(self._on_widget_closed)
        else:
            self._translate_widget.set_base_url(url)
        self._translate_widget.show()
        self._translate_widget.raise_()
        self._translate_widget.activateWindow()
        self.translation_widget_page.set_widget_running(True)

    def _on_widget_closed(self) -> None:
        if self._translate_widget:
            self._translate_widget.deleteLater()
            self._translate_widget = None
        self.translation_widget_page.set_widget_running(False)
        if self._close_requested:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self.close)

    def open_translate_settings(self) -> None:
        """兼容旧入口：切换到翻译挂件全页设置。"""
        index = next(i for i, item in enumerate(self.NAV)
                     if item[0] == "TranslationWidget")
        self.translation_widget_page.reload()
        self._switch_page(index)

    def _on_widget_prompt_applied(self, name: str, prompt: str) -> None:
        if self._translate_widget is not None:
            self._translate_widget.apply_prompt_preset(name, prompt)

    # ------------------------------------------------------------------
    def _wire_signals(self) -> None:
        # 服务控制
        self.dashboard.start_clicked.connect(self._start_server)
        self.dashboard.stop_clicked.connect(self.server.stop)
        self.dashboard.restart_clicked.connect(
            lambda: self.server.restart(self.parameters.get_params()))
        # 进程状态
        self.server.state_changed.connect(self._on_server_state)
        self.server.process_started.connect(self._start_health_check)
        self.server.pid_changed.connect(self._on_pid_changed)
        self.server.start_failed.connect(self._on_start_failed)
        self.server.exited_unexpectedly.connect(self._on_unexpected_exit)
        self.server.log_line.connect(self._on_log_line)
        # 参数变化 → 持久化
        self.parameters.params_changed.connect(self.app_config.save_params)
        # 预设
        self.presets.apply_requested.connect(self._apply_preset)
        self.presets.save_requested.connect(self._save_preset)
        # 翻译挂件页面
        self.translation_widget_page.launch_requested.connect(
            self.open_translate_widget)
        self.translation_widget_page.prompt_applied.connect(
            self._on_widget_prompt_applied)
        # Benchmark
        self.benchmark.run_requested.connect(self._start_benchmark)
        self.benchmark.stop_requested.connect(self._stop_benchmark)

    # ------------------------------------------------------------------
    def _start_server(self) -> None:
        params = self.parameters.get_params()
        errors = params.validate()
        if errors:
            QMessageBox.warning(self, "无法启动", "\n".join(errors))
            return
        self.app_config.save_params(params)
        self.server.start(params)

    def _start_health_check(self, params: ServerParams) -> None:
        self._stop_health_check()
        if self._close_requested:
            return
        self.health_checker = ApiHealthChecker(params.host, params.port, parent=self)
        self.health_checker.ready.connect(self._on_api_ready)
        self.health_checker.failed.connect(self._on_health_failed)
        self.health_checker.finished.connect(self._on_health_check_finished)
        self.health_checker.start()

    def _stop_health_check(self) -> None:
        checker = self.health_checker
        self.health_checker = None
        if checker is not None:
            checker.stop()

    def _on_health_check_finished(self) -> None:
        checker = self.sender()
        if checker is self.health_checker:
            self.health_checker = None
        checker.deleteLater()

    def _on_api_ready(self) -> None:
        if self.sender() is not self.health_checker or self._close_requested:
            return
        self.server.mark_running()
        p = self.parameters.get_params()
        self._on_log_line(f"[*] API 就绪: {p.api_base_url()}")

    def _on_health_failed(self, msg: str) -> None:
        if (self.sender() is self.health_checker
                and self.server.state == STATE_STARTING):
            self._on_log_line(f"[!] {msg}")

    def _on_server_state(self, state: str) -> None:
        if state not in (STATE_STARTING, STATE_RUNNING):
            self._stop_health_check()
        p = self.parameters.get_params()
        self.dashboard.set_server_state(
            state, pid=self.server.pid(),
            addr=p.web_ui_url(), api_url=p.api_base_url() + "/v1")

    def _on_pid_changed(self, pid: int) -> None:
        self.sys_monitor.set_server_pid(pid)
        self._on_server_state(self.server.state)

    def _on_start_failed(self, msg: str) -> None:
        self._on_log_line(f"[!] 启动失败: {msg}")
        QMessageBox.warning(self, "启动失败", msg)

    def _on_unexpected_exit(self, code: int) -> None:
        # 仅提示，GUI 不受影响
        self._on_log_line(f"[!] 检测到 llama-server 意外退出 (code={code})")

    def _on_log_line(self, line: str) -> None:
        self.logs.append_line(line)
        ev = parse_perf_line(line)
        if ev:
            self.dashboard.update_perf(ev)

    # ------------------------------------------------------------------
    def _apply_preset(self, name: str) -> None:
        merged = self.preset_manager.apply_to(
            name, self.parameters.get_params())
        self.parameters.set_params(merged)
        self._on_log_line(f"[*] 已应用预设: {name}")

    def _save_preset(self, name: str) -> None:
        self.preset_manager.put(name, self.parameters.get_params())
        self.presets.refresh()
        self._on_log_line(f"[*] 预设已保存: {name}")

    # ------------------------------------------------------------------
    def _start_benchmark(self, job: BenchJob) -> None:
        if self.server.is_running():
            QMessageBox.warning(self, "Benchmark",
                                "请先停止正在运行的 llama-server 再执行基准测试。")
            self.benchmark.set_running(False)
            return
        params = self.parameters.get_params()
        errors = params.validate()
        if errors:
            QMessageBox.warning(self, "Benchmark", "\n".join(errors))
            self.benchmark.set_running(False)
            return
        if job.mode == "asr":
            from benchmark.audio import list_audio_files
            if not list_audio_files(job.audio_dir):
                QMessageBox.warning(
                    self, "Benchmark",
                    f"音频目录中没有 wav/flac/mp3 文件：\n{job.audio_dir}\n\n"
                    "请先把参考音频放入该目录。")
                self.benchmark.set_running(False)
                return
        job.base_params = params
        self.app_config.save_params(params)
        self.benchmark.set_running(True)
        self.bench_runner = BenchmarkRunner(job, parent=self)
        self.bench_runner.sig_log.connect(self._on_log_line)
        self.bench_runner.sig_run_started.connect(self.benchmark.on_run_started)
        self.bench_runner.sig_run_result.connect(self.benchmark.on_run_result)
        # Wait for run() to return before deleting the QThread.
        self.bench_runner.finished.connect(self._on_bench_finished)
        self.bench_runner.start()

    def _stop_benchmark(self) -> None:
        if self.bench_runner and self.bench_runner.isRunning():
            self.bench_runner.stop()
            self.benchmark.progress_label.setText("正在中止（等待当前轮退出）…")

    def _on_bench_finished(self) -> None:
        self.benchmark.on_finished_all()
        if self.bench_runner:
            self.bench_runner.deleteLater()
            self.bench_runner = None

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        if not self._close_requested:
            self._close_requested = True
            self.app_config.save_params(self.parameters.get_params())
            self.setEnabled(False)
        if self._translate_widget is not None:
            self._translate_widget.close()
        if self.bench_runner and self.bench_runner.isRunning():
            self.bench_runner.stop()
        for checker in self.findChildren(ApiHealthChecker):
            checker.stop()
        self.server.shutdown()
        self.gpu_monitor.stop()
        self.sys_monitor.stop()
        threads_busy = any(thread.isRunning() or not thread.wait(0)
                           for thread in self.findChildren(QThread))
        if (self._translate_widget is not None or threads_busy
                or self.server.state != STATE_STOPPED):
            if not self._close_timer.isActive():
                self._close_timer.start()
            event.ignore()
            return
        self._close_timer.stop()
        event.accept()
