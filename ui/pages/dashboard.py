"""Dashboard 页面：实时性能与硬件监控 + 服务控制。"""
from __future__ import annotations

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)

from core.perf_parser import PerfEvent
from ui.theme import GRAY
from ui.widgets import StatCard, StatusDot


class DashboardPage(QWidget):
    start_clicked = Signal()
    stop_clicked = Signal()
    restart_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._web_url = ""
        self._api_url = ""
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        title = QLabel("Dashboard")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        # ---- 服务控制条 ----
        ctrl = QFrame()
        ctrl.setObjectName("card")
        cl = QHBoxLayout(ctrl)
        cl.setContentsMargins(14, 10, 14, 10)
        self.dot = StatusDot()
        self.status_label = QLabel("未运行")
        self.pid_label = QLabel("PID: --")
        self.pid_label.setStyleSheet(f"color: {GRAY};")
        self.addr_label = QLabel("--")
        self.addr_label.setStyleSheet(f"color: {GRAY};")
        self.btn_start = QPushButton("启动服务")
        self.btn_start.setObjectName("primaryBtn")
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setObjectName("dangerBtn")
        self.btn_restart = QPushButton("重启")
        self.btn_webui = QPushButton("打开 WebUI")
        self.btn_copy = QPushButton("复制 API URL")
        self.btn_stop.setEnabled(False)
        self.btn_restart.setEnabled(False)
        self.btn_webui.setEnabled(False)
        self.btn_start.clicked.connect(self.start_clicked)
        self.btn_stop.clicked.connect(self.stop_clicked)
        self.btn_restart.clicked.connect(self.restart_clicked)
        self.btn_webui.clicked.connect(self._open_webui)
        self.btn_copy.clicked.connect(self._copy_api)
        for w in (self.dot, self.status_label, self.pid_label, self.addr_label):
            cl.addWidget(w)
        cl.addStretch(1)
        for b in (self.btn_start, self.btn_stop, self.btn_restart,
                  self.btn_webui, self.btn_copy):
            cl.addWidget(b)
        root.addWidget(ctrl)

        # ---- tok/s 大卡片 ----
        perf = QFrame()
        perf.setObjectName("card")
        pl = QHBoxLayout(perf)
        pl.setContentsMargins(20, 16, 20, 16)
        gen_col = QVBoxLayout()
        t1 = QLabel("GENERATION TOK/S")
        t1.setObjectName("cardTitle")
        self.gen_tps = QLabel("--")
        self.gen_tps.setObjectName("bigTps")
        self.gen_detail = QLabel("")
        self.gen_detail.setObjectName("statSub")
        gen_col.addWidget(t1)
        gen_col.addWidget(self.gen_tps)
        gen_col.addWidget(self.gen_detail)
        prompt_col = QVBoxLayout()
        t2 = QLabel("PROMPT TOK/S")
        t2.setObjectName("cardTitle")
        self.prompt_tps = QLabel("--")
        self.prompt_tps.setObjectName("bigTps")
        self.prompt_tps.setStyleSheet(f"color: #0A84FF;")
        self.prompt_detail = QLabel("")
        self.prompt_detail.setObjectName("statSub")
        prompt_col.addWidget(t2)
        prompt_col.addWidget(self.prompt_tps)
        prompt_col.addWidget(self.prompt_detail)
        load_col = QVBoxLayout()
        t3 = QLabel("LOAD TIME")
        t3.setObjectName("cardTitle")
        self.load_time = QLabel("--")
        self.load_time.setObjectName("bigTps")
        self.load_time.setStyleSheet("color: #F2F2F7; font-size: 34px;")
        self.load_detail = QLabel("")
        self.load_detail.setObjectName("statSub")
        load_col.addWidget(t3)
        load_col.addWidget(self.load_time)
        load_col.addWidget(self.load_detail)
        pl.addLayout(gen_col, 1)
        pl.addLayout(prompt_col, 1)
        pl.addLayout(load_col, 1)
        root.addWidget(perf)

        # ---- 监控卡片网格 ----
        grid = QGridLayout()
        grid.setSpacing(12)
        self.card_gpu = StatCard("GPU 利用率")
        self.card_vram = StatCard("显存")
        self.card_temp = StatCard("GPU 温度")
        self.card_power = StatCard("GPU 功耗")
        self.card_clock = StatCard("GPU 频率")
        self.card_cpu = StatCard("CPU 利用率")
        self.card_ram = StatCard("内存")
        self.card_srv_cpu = StatCard("llama-server CPU")
        self.card_srv_ram = StatCard("llama-server 内存")
        cards = [self.card_gpu, self.card_vram, self.card_temp,
                 self.card_power, self.card_clock, self.card_cpu,
                 self.card_ram, self.card_srv_cpu, self.card_srv_ram]
        for i, c in enumerate(cards):
            grid.addWidget(c, i // 3, i % 3)
        root.addLayout(grid)
        root.addStretch(1)

    # ------------------------------------------------------------------
    def set_server_state(self, state: str, pid: int = 0,
                         addr: str = "", api_url: str = "") -> None:
        self.dot.set_state(state)
        text = {"stopped": "未运行", "starting": "启动中…",
                "running": "运行中", "stopping": "停止中…"}.get(state, state)
        self.status_label.setText(text)
        self.pid_label.setText(f"PID: {pid}" if pid else "PID: --")
        self.addr_label.setText(addr if pid else "--")
        self._web_url = addr
        self._api_url = api_url
        running = state == "running"
        busy = state in ("starting", "stopping")
        self.btn_start.setEnabled(state == "stopped")
        self.btn_stop.setEnabled(running or state == "starting")
        self.btn_restart.setEnabled(running)
        self.btn_webui.setEnabled(running)
        self.btn_copy.setEnabled(running)
        if busy:
            self.btn_start.setEnabled(False)

    def update_gpu(self, d: dict) -> None:
        self.card_gpu.set_value(f"{d['utilization']:.0f} %")
        used, total = d["mem_used_mb"], d["mem_total_mb"]
        self.card_vram.set_value(f"{used/1024:.1f} / {total/1024:.1f} GB",
                                 f"{used/total*100:.0f}% 已用" if total else "")
        self.card_temp.set_value(f"{d['temperature_c']:.0f} °C")
        self.card_power.set_value(f"{d['power_w']:.0f} W",
                                  f"上限 {d['power_limit_w']:.0f} W")
        self.card_clock.set_value(f"{d['clock_sm_mhz']:.0f} MHz",
                                  f"显存 {d['clock_mem_mhz']:.0f} MHz")

    def set_gpu_unavailable(self) -> None:
        for c in (self.card_gpu, self.card_vram, self.card_temp,
                  self.card_power, self.card_clock):
            c.set_value("N/A", "未检测到 nvidia-smi")

    def update_system(self, d: dict) -> None:
        self.card_cpu.set_value(f"{d['cpu_percent']:.0f} %")
        self.card_ram.set_value(
            f"{d['ram_used_gb']:.1f} / {d['ram_total_gb']:.0f} GB",
            f"{d['ram_percent']:.0f}% 已用")
        if d["proc_running"]:
            self.card_srv_cpu.set_value(f"{d['proc_cpu_percent']:.0f} %")
            self.card_srv_ram.set_value(f"{d['proc_ram_mb']:.0f} MB")
        else:
            self.card_srv_cpu.set_value("--")
            self.card_srv_ram.set_value("--")

    def update_perf(self, ev: PerfEvent) -> None:
        if ev.kind == "eval":
            self.gen_tps.setText(f"{ev.tps:.2f}")
            self.gen_detail.setText(
                f"{ev.tokens} tokens · {ev.ms/1000:.2f}s 生成")
        elif ev.kind == "prompt_eval":
            self.prompt_tps.setText(f"{ev.tps:.2f}")
            self.prompt_detail.setText(
                f"{ev.tokens} tokens · {ev.ms/1000:.2f}s 处理")
        elif ev.kind == "load":
            self.load_time.setText(f"{ev.ms/1000:.2f}s")
            self.load_detail.setText("模型加载耗时")

    # ------------------------------------------------------------------
    def _open_webui(self) -> None:
        if self._web_url:
            QDesktopServices.openUrl(QUrl(self._web_url))

    def _copy_api(self) -> None:
        if self._api_url:
            QApplication.clipboard().setText(self._api_url)
            self.btn_copy.setText("已复制 ✓")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(1500, lambda: self.btn_copy.setText("复制 API URL"))
