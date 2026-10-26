"""统一首启、重启、取消和退出的就绪协调；不依赖可变的 UI 表单。"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from core.api_health import ApiHealthChecker
from core.params import ServerParams
from core.server_manager import STATE_STARTING, ServerManager


class ServerLifecycle(QObject):
    log_line = Signal(str)

    def __init__(self, server: ServerManager, parent=None, *,
                 health_timeout: float = 300.0) -> None:
        super().__init__(parent)
        self.server = server
        self.health_timeout = health_timeout
        self.health_checker: ApiHealthChecker | None = None
        self._check_generation = 0
        self._closing = False
        server.process_started.connect(self._on_process_started)
        server.state_changed.connect(self._on_state_changed)

    def start(self, params: ServerParams) -> bool:
        return False if self._closing else self.server.start(params)

    def restart(self, params: ServerParams) -> bool:
        return False if self._closing else self.server.restart(params)

    def stop(self) -> None:
        self._cancel_check()
        self.server.stop()

    def shutdown(self) -> None:
        self._closing = True
        self._cancel_check()
        for checker in self.findChildren(ApiHealthChecker):
            checker.stop()
        self.server.shutdown()

    def _cancel_check(self) -> None:
        checker, self.health_checker = self.health_checker, None
        if checker is not None:
            checker.stop()
        # 尚在退出的旧线程保留 QObject 所有权，finished 后才删除。

    def _on_state_changed(self, state: str) -> None:
        if state != STATE_STARTING:
            self._cancel_check()

    def _on_process_started(self, generation: int, params: ServerParams) -> None:
        if (self._closing or generation != self.server.generation
                or self.server.state != STATE_STARTING):
            return
        self._cancel_check()
        checker = ApiHealthChecker(params.host, params.port,
                                   timeout=self.health_timeout, parent=self)
        self._check_generation = generation
        self.health_checker = checker
        checker.ready.connect(self._on_ready)
        checker.failed.connect(self._on_failed)
        checker.finished.connect(self._on_check_finished)
        checker.start()

    def _is_current_check(self) -> bool:
        return (not self._closing and self.sender() is self.health_checker
                and self._check_generation == self.server.generation
                and self.server.state == STATE_STARTING)

    def _on_ready(self) -> None:
        if self._is_current_check():
            params = self.server.launch_params
            if self.server.mark_running(self._check_generation):
                self.log_line.emit(f"[*] API 就绪: {params.api_base_url()}")

    def _on_failed(self, message: str) -> None:
        if self._is_current_check():
            self.log_line.emit(f"[!] {message}")
            # 明确回收本轮服务，停止完成后即可再次启动。
            self.stop()

    def _on_check_finished(self) -> None:
        checker = self.sender()
        if self._is_current_check():
            self._on_failed("API 健康检查提前结束，服务尚未就绪")
        if checker is self.health_checker:
            self.health_checker = None
        checker.deleteLater()
