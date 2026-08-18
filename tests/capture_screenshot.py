"""离屏渲染主窗口并截图，用于人工检查 UI 外观（非自动测试）。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import ui.main_window as mw
from config.app_config import AppConfig
from config.presets import PresetManager
from core.perf_parser import parse_perf_line

tmp = Path(tempfile.mkdtemp())
mw.AppConfig = lambda: AppConfig(tmp / "cfg.json")          # noqa: E731
mw.PresetManager = lambda: PresetManager(tmp / "presets.json")  # noqa: E731

app = QApplication([])
from ui.theme import APP_QSS
app.setStyleSheet(APP_QSS)

win = mw.MainWindow()
win.resize(1280, 820)

# 填充一些演示数据，让截图更接近真实状态
win.dashboard.update_gpu({"utilization": 97, "mem_used_mb": 10450,
                          "mem_total_mb": 12282, "temperature_c": 74,
                          "power_w": 132, "power_limit_w": 140,
                          "clock_sm_mhz": 2610, "clock_mem_mhz": 8001})
win.dashboard.update_system({"cpu_percent": 38, "ram_percent": 61,
                             "ram_used_gb": 39.2, "ram_total_gb": 64.0,
                             "proc_running": True, "proc_cpu_percent": 312.0,
                             "proc_ram_mb": 6144.0})
win.dashboard.update_perf(parse_perf_line(
    "llama_perf_context_print:        eval time =    2500.00 ms /  128 runs   "
    "(   19.53 ms per token,    51.20 tokens per second)"))
win.dashboard.update_perf(parse_perf_line(
    "llama_perf_context_print: prompt eval time =     400.00 ms /  256 tokens "
    "(    1.56 ms per token,   640.00 tokens per second)"))
win.dashboard.update_perf(parse_perf_line(
    "llama_perf_context_print:        load time =    1523.45 ms"))
win.dashboard.set_server_state("running", pid=23456,
                               addr="http://127.0.0.1:8080/",
                               api_url="http://127.0.0.1:8080/v1")
win.show()

out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp")


def grab_all() -> None:
    for i, name in enumerate(["dashboard", "parameters", "benchmark",
                              "logs", "presets"]):
        win._switch_page(i)
        if name == "parameters":
            win.parameters.btn_advanced.setChecked(True)  # 展开高级参数
        app.processEvents()
        win.grab().save(str(out_dir / f"ui_{name}.png"))
    app.quit()


QTimer.singleShot(300, grab_all)
app.exec()
print("screenshots saved to", out_dir)
