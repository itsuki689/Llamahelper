"""Regression tests for closing windows with real in-flight HTTP requests."""
from __future__ import annotations

import threading
import time
import socket
import sys
import wave
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

import ui.main_window as mw
import widget.dockable_translate_widget as dock
from config.app_config import AppConfig
from config.presets import PresetManager
from widget.api_client import ChatWorker
from widget.settings import WidgetSettings
from benchmark.runner import BenchJob, BenchmarkRunner
from core.params import ServerParams
from core.server_manager import port_in_use


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
