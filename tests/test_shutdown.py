"""Regression tests for closing windows with real in-flight HTTP requests."""
from __future__ import annotations

import threading
import time
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
