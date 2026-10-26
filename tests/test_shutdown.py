"""Regression tests for window, request, and server process lifetimes."""
from __future__ import annotations

import threading
import time
import socket
import sys
import wave
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import psutil
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QProcess, QThread
from shiboken6 import isValid

import ui.main_window as mw
import widget.dockable_translate_widget as dock
from config.app_config import AppConfig
from config.presets import PresetManager
from widget.api_client import ChatWorker
from widget.settings import WidgetSettings
from benchmark.runner import BenchJob, BenchmarkRunner
from core.params import ServerParams
from core.api_health import ApiHealthChecker
from core.server_manager import STATE_RUNNING, STATE_STARTING, STATE_STOPPED, port_in_use


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def pump_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def stalled_server():
    entered = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            entered.set()
            release.wait(10)
            self.close_connection = True

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", entered
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(mw, "AppConfig", lambda: AppConfig(tmp_path / "app.json"))
    monkeypatch.setattr(mw, "PresetManager", lambda: PresetManager(tmp_path / "presets.json"))
    monkeypatch.setattr(dock, "WidgetSettings", lambda: WidgetSettings(tmp_path / "widget.json"))
    window = mw.MainWindow()
    window.show()
    yield window
    window.close()
    assert pump_until(lambda: not window.isVisible())


def test_close_widget_cancels_stalled_request_before_deletion(qapp, tmp_path, stalled_server):
    url, entered = stalled_server
    widget = dock.TranslateWidget(url, WidgetSettings(tmp_path / "widget.json"))
    widget.show()
    closed = []
    widget.closed.connect(lambda: closed.append(True))
    widget.closed.connect(widget.deleteLater)
    widget.translate("hello")
    worker = widget._worker
    results, errors = [], []
    worker.result.connect(results.append)
    worker.error.connect(errors.append)
    try:
        assert pump_until(entered.is_set)
        assert not widget.close()
        assert not closed
        assert pump_until(lambda: not isValid(widget))
        assert closed == [True]
        assert not results and not errors
    finally:
        if isValid(widget):
            worker.requestInterruption()
            worker.wait(3000)
            pump_until(lambda: widget._worker is None)
            widget.close()


def test_main_window_waits_for_translation_cancellation(window, stalled_server):
    url, entered = stalled_server
    window.open_translate_widget()
    widget = window._translate_widget
    widget.set_base_url(url)
    widget.translate("hello")
    assert pump_until(entered.is_set)
    assert not window.close()
    assert window.isVisible()
    assert pump_until(lambda: not window.isVisible())
    assert window._translate_widget is None


def test_chat_request_timeout_finishes_worker(qapp, stalled_server):
    url, entered = stalled_server
    worker = ChatWorker(url, "translate", "hello", timeout=0.2)
    errors = []
    worker.error.connect(errors.append)
    worker.start()
    try:
        assert pump_until(lambda: bool(errors))
        assert "超时" in errors[0]
        assert worker.wait(1000)
    finally:
        worker.requestInterruption()
        worker.wait(3000)


def configure_fake_server(window, tmp_path, monkeypatch):
    marker = tmp_path / "post-started"
    fake_server = Path(__file__).with_name("fake_llama_server.py")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    # Invoke Python explicitly so the process tests run on Windows as well.
    monkeypatch.setattr(ServerParams, "build_command", lambda p: [
        sys._base_executable, str(fake_server), "--host", p.host,
        "--port", str(p.port), "--stall-post", str(marker),
    ])
    window.parameters.set_params(ServerParams(
        llama_server_path=sys._base_executable, model_path=str(fake_server), port=port))
    return marker, port


@pytest.mark.parametrize("mode", ["gen", "asr"])
def test_close_during_benchmark_warmup_reaps_process(window, tmp_path, monkeypatch, mode):
    marker, port = configure_fake_server(window, tmp_path, monkeypatch)
    monkeypatch.setattr("benchmark.runner.query_gpu", lambda: None)
    with wave.open(str(tmp_path / "audio.wav"), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 160)
    window._start_benchmark(BenchJob(
        ServerParams(), mode=mode, audio_dir=str(tmp_path),
        sweep_start=0, sweep_end=0, include_cpu_moe=False))
    runner = window.bench_runner
    results, finished = [], []
    runner.sig_run_result.connect(results.append)
    runner.finished.connect(lambda: finished.append(True))
    assert pump_until(marker.exists)
    process = runner._proc
    start = time.monotonic()
    assert not window.close()
    assert time.monotonic() - start < 0.5  # No blocking waits in closeEvent.
    assert pump_until(lambda: not window.isVisible())
    assert finished
    assert process.poll() is not None
    assert not port_in_use("127.0.0.1", port)
    assert results and results[0]["status"] == "中止"


def test_completion_signal_does_not_destroy_a_running_benchmark(window, tmp_path, monkeypatch):
    configure_fake_server(window, tmp_path, monkeypatch)
    release = threading.Event()
    announced = threading.Event()

    def delayed_return(runner):
        runner.sig_finished_all.emit()
        announced.set()
        release.wait(10)

    monkeypatch.setattr(BenchmarkRunner, "run", delayed_return)
    window._start_benchmark(BenchJob(ServerParams()))
    runner = window.bench_runner
    # Process queued completion signals while run() is still on the stack.
    try:
        assert pump_until(announced.is_set)
        assert window.bench_runner is runner and runner.isRunning()
        assert not window.close()
        assert window.isVisible()
        QApplication.processEvents()
        assert window.isVisible()
        release.set()
        assert pump_until(lambda: not window.isVisible())
    finally:
        release.set()
        if isValid(runner):
            runner.wait(3000)


@pytest.mark.parametrize("change_port", [False, True])
def test_restart_checks_new_process_and_restores_dashboard(window, tmp_path, monkeypatch, change_port):
    _, port = configure_fake_server(window, tmp_path, monkeypatch)
    launched_ports = []
    window.server.process_started.connect(lambda _generation, p: launched_ports.append(p.port))
    window._start_server()
    assert pump_until(lambda: window.server.state == STATE_RUNNING)
    first_pid = window.server.pid()
    if change_port:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        params = window.parameters.get_params()
        params.port = port
        window.parameters.set_params(params)
    window.dashboard.btn_restart.click()
    assert pump_until(lambda: window.server.state == STATE_RUNNING
                      and window.server.pid() != first_pid, timeout=12)
    assert len(launched_ports) == 2 and launched_ports[-1] == port
    assert window.dashboard.btn_webui.isEnabled()
    assert window.dashboard.btn_copy.isEnabled()
    assert window.dashboard.btn_restart.isEnabled()
    assert str(port) in window.dashboard._api_url
    window.server.stop()
    assert pump_until(lambda: window.server.state == STATE_STOPPED, timeout=8)


@pytest.mark.parametrize("late_result", ["ready", "failed"])
def test_stale_health_result_cannot_change_new_launch(window, tmp_path, monkeypatch, late_result):
    configure_fake_server(window, tmp_path, monkeypatch)
    # Hold results so the test controls their order across two real processes.
    monkeypatch.setattr(ApiHealthChecker, "start", lambda self: None)
    window._start_server()
    assert pump_until(lambda: window.lifecycle.health_checker is not None)
    old_checker = window.lifecycle.health_checker
    old_generation = window.server.generation
    window.lifecycle.restart(window.parameters.get_params())
    assert pump_until(lambda: window.lifecycle.health_checker not in (None, old_checker))
    new_checker = window.lifecycle.health_checker

    def emit_late():
        if late_result == "ready":
            old_checker.ready.emit()
        else:
            old_checker.failed.emit("old failure")

    emit_late()
    assert window.server.state == STATE_STARTING
    assert not window.server.mark_running(old_generation)
    assert not window.dashboard.btn_webui.isEnabled()
    new_checker.ready.emit()
    assert window.server.state == STATE_RUNNING
    emit_late()
    assert window.server.state == STATE_RUNNING
    assert "old failure" not in window.logs.view.toPlainText()


def test_stop_during_start_rejects_late_success(window, tmp_path, monkeypatch):
    _, port = configure_fake_server(window, tmp_path, monkeypatch)
    monkeypatch.setattr(ApiHealthChecker, "start", lambda self: None)
    states = []
    window.server.state_changed.connect(states.append)
    window._start_server()
    assert pump_until(lambda: window.lifecycle.health_checker is not None)
    checker = window.lifecycle.health_checker
    pid = window.server.pid()
    window.dashboard.btn_stop.click()
    checker.ready.emit()
    assert pump_until(lambda: window.server.state == STATE_STOPPED)
    checker.ready.emit()
    assert STATE_RUNNING not in states
    assert not psutil.pid_exists(pid)
    assert not port_in_use("127.0.0.1", port)
    assert window.dashboard.btn_start.isEnabled()
    assert not window.dashboard.btn_copy.isEnabled()


def test_start_uses_snapshot_for_health_and_addresses(window, tmp_path, monkeypatch):
    _, port = configure_fake_server(window, tmp_path, monkeypatch)
    monkeypatch.setattr(ApiHealthChecker, "start", lambda self: None)
    window._start_server()
    params = window.parameters.get_params()
    params.port = port + 1 if port < 65535 else port - 1
    params.model_path = str(tmp_path / "edited-model.gguf")
    window.parameters.set_params(params)
    assert pump_until(lambda: window.lifecycle.health_checker is not None)
    checker = window.lifecycle.health_checker
    assert checker._url == f"http://127.0.0.1:{port}/health"
    snapshot = window.server.launch_params
    snapshot.port = params.port  # Callers also receive a defensive copy.
    checker.ready.emit()
    assert window.server.state == STATE_RUNNING
    assert window.dashboard._api_url == f"http://127.0.0.1:{port}/v1"
    assert window.dashboard._web_url == f"http://127.0.0.1:{port}/"
    assert f"API 就绪: http://127.0.0.1:{port}" in window.logs.view.toPlainText()


def test_checker_finishes_without_result_recovers(window, tmp_path, monkeypatch):
    configure_fake_server(window, tmp_path, monkeypatch)
    monkeypatch.setattr(ApiHealthChecker, "run", lambda self: None)
    window._start_server()
    assert pump_until(lambda: window.server.state == STATE_STOPPED)
    assert "健康检查提前结束" in window.logs.view.toPlainText()
    assert window.dashboard.btn_start.isEnabled()


def test_health_timeout_allows_another_start(window, tmp_path, monkeypatch):
    _, port = configure_fake_server(window, tmp_path, monkeypatch)
    window.lifecycle.health_timeout = 0
    window._start_server()
    assert pump_until(lambda: window.server.state == STATE_STOPPED, timeout=8)
    assert "等待 API 就绪超时" in window.logs.view.toPlainText()
    assert window.server._proc is None
    assert not port_in_use("127.0.0.1", port)
    assert window.dashboard.btn_start.isEnabled()
    window.lifecycle.health_timeout = 10
    window.dashboard.btn_start.click()
    assert pump_until(lambda: window.server.state == STATE_RUNNING)
    assert window.dashboard.btn_webui.isEnabled()


def test_old_process_signals_cannot_retire_or_kill_new_process(window, tmp_path, monkeypatch):
    configure_fake_server(window, tmp_path, monkeypatch)
    window._start_server()
    assert pump_until(lambda: window.server.state == STATE_RUNNING)
    old_proc, old_timer = window.server._proc, window.server._kill_timer
    # Keep the retired QObject alive to inject delayed signals deterministically.
    monkeypatch.setattr(old_proc, "deleteLater", lambda: None)
    window.dashboard.btn_restart.click()
    assert pump_until(lambda: window.server.state == STATE_RUNNING, timeout=10)
    new_proc = window.server._proc
    old_timer.timeout.emit()
    old_proc.started.emit()
    old_proc.errorOccurred.emit(QProcess.ProcessError.FailedToStart)
    old_proc.finished.emit(1, QProcess.ExitStatus.CrashExit)
    assert window.server._proc is new_proc
    assert new_proc.state() == QProcess.ProcessState.Running
    assert window.server.state == STATE_RUNNING


def test_explicit_stop_cancels_pending_restart(window, tmp_path, monkeypatch):
    configure_fake_server(window, tmp_path, monkeypatch)
    window._start_server()
    assert pump_until(lambda: window.server.state == STATE_RUNNING)
    generation = window.server.generation
    window.dashboard.btn_restart.click()
    window.dashboard.btn_stop.click()
    assert pump_until(lambda: window.server.state == STATE_STOPPED)
    assert window.server.generation == generation
    assert window.server._pending_restart is None


@pytest.mark.parametrize("wait_for_process", [False, True])
def test_close_server_reaps_process_and_threads(window, tmp_path, monkeypatch, wait_for_process):
    _, port = configure_fake_server(window, tmp_path, monkeypatch)
    window._start_server()
    pid = window.server.pid()
    if wait_for_process:
        assert pump_until(lambda: window.server.state == STATE_RUNNING)
        pid = window.server.pid()
    started = time.monotonic()
    window.close()
    assert time.monotonic() - started < 0.5
    assert pump_until(lambda: not window.isVisible(), timeout=8)
    assert window.server.state == STATE_STOPPED
    assert window.server._proc is None
    assert not port_in_use("127.0.0.1", port)
    if pid:
        assert not psutil.pid_exists(pid)
    assert all(not t.isRunning() and t.wait(0) for t in window.findChildren(QThread))
    assert not window.lifecycle.start(window.parameters.get_params())
