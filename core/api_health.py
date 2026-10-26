"""可协作取消、具有总期限的 API 就绪检查。"""
from __future__ import annotations

import time
from threading import Event

from PySide6.QtCore import QEventLoop, QThread, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from core.params import ServerParams


class ApiHealthChecker(QThread):
    ready = Signal()
    failed = Signal(str)

    def __init__(self, host: str, port: int, timeout: float = 300.0,
                 parent=None) -> None:
        super().__init__(parent)
        self._url = ServerParams(host=host, port=port).api_base_url() + "/health"
        self._timeout = timeout
        self._cancelled = Event()

    def stop(self) -> None:
        # Event 也覆盖 start() 前就取消的情况；不使用 QThread.terminate()。
        self._cancelled.set()
        self.requestInterruption()

    def run(self) -> None:
        end = time.monotonic() + self._timeout
        last_error = "尚未收到响应"
        try:
            manager = QNetworkAccessManager()
            while not self._cancelled.is_set() and time.monotonic() < end:
                reply = manager.get(QNetworkRequest(QUrl(self._url)))
                loop = QEventLoop()
                reply.finished.connect(loop.quit)
                poll = QTimer()
                poll.setInterval(25)
                deadline = min(end, time.monotonic() + 2.0)

                def check_cancel() -> None:
                    if self._cancelled.is_set() or time.monotonic() >= deadline:
                        reply.abort()
                        loop.quit()

                poll.timeout.connect(check_cancel)
                poll.start()
                if not reply.isFinished():
                    loop.exec()
                poll.stop()
                status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
                healthy = (status == 200
                           and reply.error() == QNetworkReply.NetworkError.NoError)
                last_error = f"HTTP {status}" if status else reply.errorString()
                reply.deleteLater()
                if self._cancelled.is_set():
                    return
                if healthy and time.monotonic() < end:
                    self.ready.emit()
                    return
                self._cancelled.wait(min(0.5, max(0.0, end - time.monotonic())))
            if not self._cancelled.is_set():
                self.failed.emit(f"等待 API 就绪超时：{self._url}（{last_error}）")
        except Exception as exc:
            if not self._cancelled.is_set():
                self.failed.emit(f"API 健康检查失败：{self._url}（{exc}）")
