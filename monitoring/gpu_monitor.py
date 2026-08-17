"""NVIDIA GPU 监控：定时调用 nvidia-smi，不存在时正常降级。"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

_GPU_FIELDS = (
    "utilization.gpu",
    "memory.used",
    "memory.total",
    "temperature.gpu",
    "power.draw",
    "power.limit",
    "clocks.sm",
    "clocks.mem",
)

_NVSMI_FALLBACK = Path(r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe")

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def find_nvidia_smi() -> str | None:
    """定位 nvidia-smi 可执行文件，找不到返回 None。"""
    found = shutil.which("nvidia-smi")
    if found:
        return found
    if sys.platform == "win32" and _NVSMI_FALLBACK.is_file():
        return str(_NVSMI_FALLBACK)
    return None


def query_gpu(timeout: float = 3.0) -> dict | None:
    """查询一次 GPU 状态；nvidia-smi 不存在或执行失败返回 None。"""
    exe = find_nvidia_smi()
    if not exe:
        return None
    cmd = [exe, f"--query-gpu={','.join(_GPU_FIELDS)}",
           "--format=csv,noheader,nounits"]
    try:
        out = subprocess.check_output(
            cmd, timeout=timeout, creationflags=_NO_WINDOW,
            stderr=subprocess.DEVNULL, text=True)
    except (subprocess.SubprocessError, OSError):
        return None
    line = out.strip().splitlines()[0] if out.strip() else ""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < len(_GPU_FIELDS):
        return None

    def num(s: str) -> float:
        try:
            return float(s)
        except ValueError:
            return 0.0

    return {
        "available": True,
        "utilization": num(parts[0]),      # %
        "mem_used_mb": num(parts[1]),
        "mem_total_mb": num(parts[2]),
        "temperature_c": num(parts[3]),
        "power_w": num(parts[4]),
        "power_limit_w": num(parts[5]),
        "clock_sm_mhz": num(parts[6]),
        "clock_mem_mhz": num(parts[7]),
    }


class GpuMonitor(QThread):
    """后台线程：每秒查询一次 GPU，通过信号推送结果。"""

    stats = Signal(dict)        # query_gpu 的返回字典
    unavailable = Signal()      # nvidia-smi 不可用（只发一次）

    def __init__(self, interval: float = 1.0, parent=None) -> None:
        super().__init__(parent)
        self._interval = interval
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        notified = False
        while self._running:
            data = query_gpu()
            if data is None:
                if not notified:
                    notified = True
                    self.unavailable.emit()
            else:
                self.stats.emit(data)
            # 分段 sleep，保证 stop 后能快速退出
            waited = 0.0
            while self._running and waited < self._interval:
                time.sleep(0.1)
                waited += 0.1
