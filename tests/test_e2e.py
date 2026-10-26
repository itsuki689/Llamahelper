"""真实子进程 + MainWindow 的跨平台生命周期验收，无模型/GPU 依赖。"""
from __future__ import annotations

import json
import socket
import time
from dataclasses import replace

import psutil
import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

import ui.main_window as mw
from config.app_config import AppConfig
from config.presets import PresetManager
from core.api_health import ApiHealthChecker
from core.params import ServerParams
from core.server_manager import STATE_RUNNING, STATE_STARTING, STATE_STOPPED


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def pump_until(condition, timeout=12):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def params(fake_exe, tmp_path):
    model = tmp_path / "model with spaces.gguf"
    model.write_text("placeholder, never loaded", encoding="utf-8")
    return ServerParams(llama_server_path=str(fake_exe), model_path=str(model),
                        host="127.0.0.1", port=free_port())


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(mw, "AppConfig", lambda: AppConfig(tmp_path / "cfg.json"))
    monkeypatch.setattr(mw, "PresetManager", lambda: PresetManager(tmp_path / "presets.json"))
    monkeypatch.setattr("monitoring.gpu_monitor.query_gpu", lambda: None)
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *args: None)
    win = mw.MainWindow()
    win.lifecycle.health_timeout = 5
    pids = []
    win.server.process_started.connect(lambda _g, _p: pids.append(win.server.pid()))
    win.show()
    yield win
    win.close()
    assert pump_until(lambda: not win.isVisible())
    assert win.server.state == STATE_STOPPED and win.server._proc is None
    assert all(not psutil.pid_exists(pid) for pid in pids)
    assert all(not t.isRunning() and t.wait(0) for t in win.findChildren(QThread))


def assert_stopped_controls(win):
    assert win.server.state == STATE_STOPPED
    assert win.dashboard.btn_start.isEnabled()
    assert not win.dashboard.btn_stop.isEnabled()
    assert not win.dashboard.btn_restart.isEnabled()
    assert not win.dashboard.btn_webui.isEnabled()
    assert not win.dashboard.btn_copy.isEnabled()
    assert win.dashboard.pid_label.text() == "PID: --"


def assert_running_controls(win, params):
    assert win.server.state == STATE_RUNNING
    assert not win.dashboard.btn_start.isEnabled()
    assert win.dashboard.btn_stop.isEnabled()
    assert win.dashboard.btn_restart.isEnabled()
    assert win.dashboard.btn_webui.isEnabled()
    assert win.dashboard.btn_copy.isEnabled()
    assert win.dashboard._web_url == params.web_ui_url()
    assert win.dashboard._api_url == params.api_base_url() + "/v1"
    win.dashboard.btn_copy.click()
    assert QApplication.clipboard().text() == params.api_base_url() + "/v1"


def test_full_start_stop_via_mainwindow(window, params, tmp_path):
    params.extra_args = "--startup-delay 0.1 --ready-delay 0.1"
    window.parameters.set_params(params)
    assert_stopped_controls(window)
    window.dashboard.btn_start.click()
    assert window.server.state == STATE_STARTING
    assert not window.dashboard.btn_copy.isEnabled()
    assert pump_until(lambda: window.server.state == STATE_RUNNING)
    assert_running_controls(window, params)
    assert "server_listen" in window.logs.view.toPlainText()
    assert f"API 就绪: {params.api_base_url()}" in window.logs.view.toPlainText()
    window.dashboard.btn_stop.click()
    assert pump_until(lambda: window.server.state == STATE_STOPPED)
    assert_stopped_controls(window)
    assert (tmp_path / "cfg.json").exists()


def test_restart_reaps_old_process_and_freezes_new_parameters(window, params, tmp_path, monkeypatch):
    window.parameters.set_params(params)
    window.dashboard.btn_start.click()
    assert pump_until(lambda: window.server.state == STATE_RUNNING)
    old_pid = window.server.pid()
    old_reaped_before_start = []
    window.server.process_started.connect(
        lambda _g, _p: old_reaped_before_start.append(not psutil.pid_exists(old_pid)))
    model = tmp_path / "second model with spaces.gguf"
    model.write_text("second placeholder", encoding="utf-8")
    record = tmp_path / "second launch.json"
    new_params = replace(params, port=free_port(), model_path=str(model),
                         context_size=4096,
                         extra_args=f'--ready-delay 0.1 --record-launch "{record}"')
    window.parameters.set_params(new_params)
    window.dashboard.btn_restart.click()
    # Further edits while the old process is stopping cannot change this launch.
    window.parameters.set_params(replace(params, port=free_port(), context_size=2048))
    assert pump_until(lambda: window.server.state == STATE_RUNNING
                      and window.server.pid() != old_pid)
    assert old_reaped_before_start == [True]
    launched = json.loads(record.read_text(encoding="utf-8"))
    assert launched["pid"] == window.server.pid()
    assert launched["port"] == new_params.port
    assert launched["model"] == str(model)
    assert launched["argv"][launched["argv"].index("-c") + 1] == "4096"
    assert_running_controls(window, new_params)
    opened = []
    monkeypatch.setattr("ui.pages.dashboard.QDesktopServices.openUrl", lambda url: opened.append(url.toString()))
    window.dashboard.btn_webui.click()
    assert opened == [new_params.web_ui_url()]
    assert f"API 就绪: {new_params.api_base_url()}" in window.logs.view.toPlainText()


@pytest.mark.parametrize("phase", ["process_start", "health_request"])
def test_stop_during_start_leaves_no_process_or_running_state(window, params, tmp_path, phase):
    marker = tmp_path / "health entered"
    params.extra_args = f'--health-delay 30 --health-marker "{marker}"'
    states = []
    window.server.state_changed.connect(states.append)
    window.parameters.set_params(params)
    window.dashboard.btn_start.click()
    if phase == "health_request":
        assert pump_until(marker.exists)
    window.dashboard.btn_stop.click()
    assert pump_until(lambda: window.server.state == STATE_STOPPED)
    assert pump_until(lambda: not any(t.isRunning() for t in window.findChildren(ApiHealthChecker)))
    assert STATE_RUNNING not in states
    assert window.server._proc is None
    assert_stopped_controls(window)


@pytest.mark.parametrize("response", ["loading", "stalled"])
def test_health_timeout_reaps_process_and_can_retry(window, params, tmp_path, response):
    marker = tmp_path / "health entered"
    behavior = "--never-ready" if response == "loading" else "--health-delay 30"
    params.extra_args = f'{behavior} --health-marker "{marker}"'
    window.lifecycle.health_timeout = 1
    window.parameters.set_params(params)
    window.dashboard.btn_start.click()
    assert pump_until(marker.exists)
    old_pid = window.server.pid()
    assert pump_until(lambda: window.server.state == STATE_STOPPED)
    assert "等待 API 就绪超时" in window.logs.view.toPlainText()
    assert not psutil.pid_exists(old_pid)
    assert_stopped_controls(window)
    window.lifecycle.health_timeout = 5
    params.extra_args = ""
    window.parameters.set_params(params)
    window.dashboard.btn_start.click()
    assert pump_until(lambda: window.server.state == STATE_RUNNING)
    assert_running_controls(window, params)


@pytest.mark.parametrize("startup_delay", [0, 2])
def test_early_exit_restores_controls_and_logs_reason(window, params, startup_delay):
    params.extra_args = f"--never-ready --startup-delay {startup_delay} --exit-after 0.2"
    exits = []
    window.server.exited_unexpectedly.connect(exits.append)
    window.parameters.set_params(params)
    window.dashboard.btn_start.click()
    assert pump_until(lambda: window.server.state == STATE_STOPPED)
    assert exits == [17]
    assert "early exit 17" in window.logs.view.toPlainText()
    assert "意外退出" in window.logs.view.toPlainText()
    assert_stopped_controls(window)
    params.extra_args = ""
    window.parameters.set_params(params)
    window.dashboard.btn_start.click()
    assert pump_until(lambda: window.server.state == STATE_RUNNING)


def test_unexecutable_server_restores_controls_and_can_retry(window, params, tmp_path):
    bad_exe = tmp_path / "not an executable.exe"
    bad_exe.write_text("not a program", encoding="utf-8")
    window.parameters.set_params(replace(params, llama_server_path=str(bad_exe)))
    failures = []
    window.server.start_failed.connect(failures.append)
    window.dashboard.btn_start.click()
    assert pump_until(lambda: bool(failures))
    assert "无法执行" in failures[0]
    assert "无法执行" in window.logs.view.toPlainText()
    assert_stopped_controls(window)
    window.parameters.set_params(params)
    window.dashboard.btn_start.click()
    assert pump_until(lambda: window.server.state == STATE_RUNNING)


def test_close_with_health_request_inflight_reaps_everything(window, params, tmp_path):
    marker = tmp_path / "health entered"
    params.extra_args = f'--health-delay 30 --health-marker "{marker}"'
    window.parameters.set_params(params)
    window.dashboard.btn_start.click()
    assert pump_until(marker.exists)
    pid = window.server.pid()
    start = time.monotonic()
    assert not window.close()
    assert time.monotonic() - start < 0.5
    assert pump_until(lambda: not window.isVisible())
    assert not psutil.pid_exists(pid)
    assert window.server.state == STATE_STOPPED
    assert window.server._proc is None
    assert all(not t.isRunning() and t.wait(0) for t in window.findChildren(QThread))
