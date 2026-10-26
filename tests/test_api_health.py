"""健康检查总期限与协作取消，包括已连接但不返回响应的 API。"""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PySide6.QtWidgets import QApplication

from core.api_health import ApiHealthChecker


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def pump_until(condition, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def stalled_health():
    entered, release = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            entered.set()
            release.wait(5)
            self.close_connection = True

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server.server_port, entered
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)
        assert not thread.is_alive()


@pytest.mark.parametrize("cancel", [False, True])
def test_stalled_health_is_bounded_and_cancellable(qapp, stalled_health, cancel):
    port, entered = stalled_health
    checker = ApiHealthChecker("127.0.0.1", port, timeout=30 if cancel else 0.3)
    ready, failed = [], []
    checker.ready.connect(lambda: ready.append(True))
    checker.failed.connect(failed.append)
    start = time.monotonic()
    checker.start()
    try:
        assert pump_until(entered.is_set)
        if cancel:
            checker.stop()
        assert pump_until(lambda: checker.isFinished())
        assert checker.wait(1000)
        QApplication.processEvents()
        assert time.monotonic() - start < 1.5
        assert not ready
        if cancel:
            assert not failed
        else:
            assert len(failed) == 1 and "超时" in failed[0]
            assert str(port) in failed[0]
    finally:
        checker.stop()
        assert checker.wait(3000)


def test_cancel_before_start_produces_no_result(qapp):
    checker = ApiHealthChecker("127.0.0.1", 1)
    results = []
    checker.ready.connect(lambda: results.append("ready"))
    checker.failed.connect(results.append)
    checker.stop()
    checker.start()
    assert checker.wait(1000)
    QApplication.processEvents()
    assert not results
