"""系统监控：psutil 采集 CPU/RAM 以及 llama-server 进程占用。"""
from __future__ import annotations

import time

import psutil
from PySide6.QtCore import QThread, Signal


class SystemMonitor(QThread):
    """后台线程：每秒推送一次系统与 llama-server 进程的资源占用。"""

    stats = Signal(dict)

    def __init__(self, interval: float = 1.0, parent=None) -> None:
        super().__init__(parent)
        self._interval = interval
        self._running = True
        self._pid = 0

    def set_server_pid(self, pid: int) -> None:
        self._pid = pid

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        psutil.cpu_percent(interval=None)  # 在采样线程内预热
        proc: psutil.Process | None = None
        while self._running:
            data: dict = {
                "cpu_percent": psutil.cpu_percent(interval=None),
                "ram_percent": psutil.virtual_memory().percent,
                "ram_used_gb": psutil.virtual_memory().used / (1024 ** 3),
                "ram_total_gb": psutil.virtual_memory().total / (1024 ** 3),
                "proc_running": False,
                "proc_cpu_percent": 0.0,
                "proc_ram_mb": 0.0,
            }
            pid = self._pid
            if proc is not None and proc.pid != pid:
                proc = None
            if pid:
                try:
                    if proc is None or not proc.is_running():
                        proc = psutil.Process(pid)
                    with proc.oneshot():
                        data["proc_running"] = proc.is_running()
                        data["proc_cpu_percent"] = proc.cpu_percent(interval=None)
                        data["proc_ram_mb"] = proc.memory_info().rss / (1024 ** 2)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    proc = None
                    data["proc_running"] = False
            self.stats.emit(data)
            waited = 0.0
            while self._running and waited < self._interval:
                time.sleep(0.1)
                waited += 0.1
