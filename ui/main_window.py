"""主窗口：侧边导航 + 页面栈 + 各模块信号接线。"""
from __future__ import annotations

import time
import urllib.error
import urllib.request

from PySide6.QtCore import Qt, QThread, Signal
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


class ApiHealthChecker(QThread):
    """后台轮询 /health，确认 llama-server API 就绪，不阻塞 UI。"""

    ready = Signal()
    failed = Signal(str)

    def __init__(self, host: str, port: int, timeout: float = 300.0,
                 parent=None) -> None:
        super().__init__(parent)
        self._url = f"http://{host}:{port}/health"
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
                        self.ready.emit()
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.5)
        if self._running:
            self.failed.emit("等待 API 就绪超时")


class MainWindow(QMainWindow):
    NAV = [("Dashboard", "仪表盘"), ("Parameters", "参数"),
           ("Benchmark", "性能测试"), ("Logs", "日志"), ("Presets", "预设")]

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LlamaHelper — llama.cpp 调参与性能测试")
        self.resize(1180, 780)

        # ---- 核心组件 ----
        self.app_config = AppConfig()
        self.preset_manager = PresetManager()
        self.server = ServerManager(self)
        self.health_checker: ApiHealthChecker | None = None
        self.bench_runner: BenchmarkRunner | None = None
        self._translate_widget = None

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
        title = QLabel("LlamaHelper")
        title.setObjectName("appTitle")
        subtitle = QLabel("llama.cpp 调参 · 性能测试")
        subtitle.setObjectName("appSubtitle")
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
        # 翻译挂件入口
        self.btn_widget = QPushButton("翻译挂件")
        self.btn_widget.setObjectName("navBtn")
        self.btn_widget.clicked.connect(self.open_translate_widget)
        sb.addWidget(self.btn_widget)

        # 页面
        self.dashboard = DashboardPage()
        self.parameters = ParametersPage()
        self.benchmark = BenchmarkPage()
        self.logs = LogsPage()
        self.presets = PresetsPage(self.preset_manager)
        for p in (self.dashboard, self.parameters, self.benchmark,
                  self.logs, self.presets):
            self.stack.addWidget(p)

        lay.addWidget(sidebar)
        lay.addWidget(self.stack, 1)
        self._switch_page(0)

    def _switch_page(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate(self.nav_buttons):
            b.setChecked(i == idx)

    def open_translate_widget(self) -> None:
        """启动/唤起翻译挂件，跟随当前参数页的 host/port。"""
        from widget.translate_widget import TranslateWidget

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

    def _on_widget_closed(self) -> None:
        if self._translate_widget:
            self._translate_widget.deleteLater()
            self._translate_widget = None

    # ------------------------------------------------------------------
    def _wire_signals(self) -> None:
        # 服务控制
        self.dashboard.start_clicked.connect(self._start_server)
        self.dashboard.stop_clicked.connect(self.server.stop)
        self.dashboard.restart_clicked.connect(
            lambda: self.server.restart(self.parameters.get_params()))
        # 进程状态
        self.server.state_changed.connect(self._on_server_state)
        self.server.pid_changed.connect(self._on_pid_changed)
        self.server.start_failed.connect(self._on_start_failed)
        self.server.exited_unexpectedly.connect(self._on_unexpected_exit)
        self.server.log_line.connect(self._on_log_line)
        # 参数变化 → 持久化
        self.parameters.params_changed.connect(self.app_config.save_params)
        # 预设
        self.presets.apply_requested.connect(self._apply_preset)
        self.presets.save_requested.connect(self._save_preset)
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
        if self.server.start(params):
            self._start_health_check(params)

    def _start_health_check(self, params: ServerParams) -> None:
        if self.health_checker and self.health_checker.isRunning():
            self.health_checker.stop()
            self.health_checker.wait(2000)
        self.health_checker = ApiHealthChecker(params.host, params.port, parent=self)
        self.health_checker.ready.connect(self._on_api_ready)
        self.health_checker.failed.connect(self._on_health_failed)
        self.health_checker.start()

    def _on_api_ready(self) -> None:
        self.server.mark_running()
        p = self.parameters.get_params()
        self._on_log_line(f"[*] API 就绪: {p.api_base_url()}")

    def _on_health_failed(self, msg: str) -> None:
        if self.server.state == STATE_STARTING:
            self._on_log_line(f"[!] {msg}")

    def _on_server_state(self, state: str) -> None:
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
        job.base_params = params
        self.app_config.save_params(params)
        self.benchmark.set_running(True)
        self.bench_runner = BenchmarkRunner(job, parent=self)
        self.bench_runner.sig_log.connect(self._on_log_line)
        self.bench_runner.sig_run_started.connect(self.benchmark.on_run_started)
        self.bench_runner.sig_run_result.connect(self.benchmark.on_run_result)
        self.bench_runner.sig_finished_all.connect(self._on_bench_finished)
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
        self.app_config.save_params(self.parameters.get_params())
        if self._translate_widget is not None:
            self._translate_widget.close()
        if self.bench_runner and self.bench_runner.isRunning():
            self.bench_runner.stop()
            self.bench_runner.wait(8000)
        if self.health_checker and self.health_checker.isRunning():
            self.health_checker.stop()
            self.health_checker.wait(2000)
        if self.server.is_running():
            self.server.stop()
            self.server.wait_stopped(6000)
        self.gpu_monitor.stop()
        self.sys_monitor.stop()
        self.gpu_monitor.wait(2000)
        self.sys_monitor.wait(2000)
        event.accept()
