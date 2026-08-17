"""端到端测试：通过 MainWindow 完整走 启动→API就绪→运行→停止 流程。"""
from __future__ import annotations

import socket
import stat
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

import ui.main_window as mw
from config.app_config import AppConfig
from config.presets import PresetManager
from core.params import ServerParams
from core.server_manager import STATE_RUNNING, STATE_STOPPED

FAKE_SERVER = Path(__file__).parent / "fake_llama_server.py"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def pump_until(cond, timeout: float = 20.0) -> bool:
    app = QApplication.instance()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_full_start_stop_via_mainwindow(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(mw, "AppConfig",
                        lambda: AppConfig(tmp_path / "cfg.json"))
    monkeypatch.setattr(mw, "PresetManager",
                        lambda: PresetManager(tmp_path / "presets.json"))

    launcher = tmp_path / "fake_llama_server"
    launcher.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_SERVER}" "$@"\n')
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    win = mw.MainWindow()
    win.parameters.set_params(ServerParams(
        llama_server_path=str(launcher),
        model_path=str(FAKE_SERVER),
        host="127.0.0.1", port=port))

    win._start_server()
    # 健康检查线程应自动把状态推进到 running
    assert pump_until(lambda: win.server.state == STATE_RUNNING)
    assert win.dashboard.btn_webui.isEnabled()
    assert "PID: --" not in win.dashboard.pid_label.text()

    # 日志应包含假服务器输出与 API 就绪提示
    assert pump_until(lambda: "API 就绪" in win.logs.view.toPlainText())

    win.server.stop()
    assert pump_until(lambda: win.server.state == STATE_STOPPED)
    assert win.dashboard.btn_start.isEnabled()

    # 配置已保存
    assert (tmp_path / "cfg.json").exists()
    win.close()
    QApplication.instance().processEvents()
