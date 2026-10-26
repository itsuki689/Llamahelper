"""QProcess 所有权与串行启停；API 就绪由 ServerLifecycle 协调。"""
from __future__ import annotations

import socket
from dataclasses import replace

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from core.params import ServerParams
from core.network import client_host

STATE_STOPPED = "stopped"
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_STOPPING = "stopping"


def port_in_use(host: str, port: int, timeout: float = 0.4) -> bool:
    """检测 TCP 端口是否已被占用。"""
    try:
        with socket.create_connection((client_host(host), port), timeout=timeout):
            return True
    except OSError:
        return False


class ServerManager(QObject):
    """每轮独占一个 QProcess；只有回收旧进程后才允许下一轮启动。"""

    log_line = Signal(str)
    state_changed = Signal(str)
    pid_changed = Signal(int)
    start_failed = Signal(str)
    exited_unexpectedly = Signal(int)
    process_started = Signal(int, object)  # 启动代次、当轮 ServerParams 快照

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._kill_timer: QTimer | None = None
        self._state = STATE_STOPPED
        self._generation = 0
        self._buf = ""
        self._intentional_stop = False
        self._closing = False
        self._launch_params: ServerParams | None = None
        self._pending_restart: ServerParams | None = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def launch_params(self) -> ServerParams | None:
        return replace(self._launch_params) if self._launch_params else None

    def is_running(self) -> bool:
        return self._state in (STATE_STARTING, STATE_RUNNING)

    def pid(self) -> int:
        # stopping 期间仍拥有该进程，监控和关闭流程不能把它当成已退出。
        return int(self._proc.processId()) if self._proc else 0

    def start(self, params: ServerParams) -> bool:
        if self._closing:
            return False
        if self._state != STATE_STOPPED or self._proc is not None:
            self.start_failed.emit("服务尚未停止，拒绝重复启动")
            return False
        params = replace(params)
        errors = params.validate()
        if errors:
            self.start_failed.emit("；".join(errors))
            return False
        if port_in_use(params.host, params.port):
            self.start_failed.emit(
                f"端口 {params.port} 已被占用，请更换端口或先停止占用进程")
            return False

        cmd = params.build_command()
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        proc.readyReadStandardOutput.connect(self._on_ready_read)
        proc.started.connect(self._on_started)
        proc.errorOccurred.connect(self._on_error)
        proc.finished.connect(self._on_finished)
        timer = QTimer(proc)
        timer.setSingleShot(True)
        timer.setInterval(4000)
        timer.timeout.connect(self._force_kill)
        self._proc, self._kill_timer = proc, timer
        self._generation += 1
        self._intentional_stop = False
        self._buf = ""
        self._launch_params = params
        self._set_state(STATE_STARTING)
        self.log_line.emit("$ " + params.command_display())
        proc.start(cmd[0], cmd[1:])
        return True

    def mark_running(self, generation: int) -> bool:
        """只接受当前代次且仍存活的进程的健康检查结果。"""
        if (generation != self._generation or self._state != STATE_STARTING
                or self._proc is None
                or self._proc.state() != QProcess.ProcessState.Running):
            return False
        self._set_state(STATE_RUNNING)
        return True

    def stop(self) -> None:
        # 显式停止也会撤销停止中排队的重启。
        self._pending_restart = None
        self._stop_process()

    def _stop_process(self) -> None:
        if self._state in (STATE_STOPPED, STATE_STOPPING):
            return
        self._intentional_stop = True
        self._set_state(STATE_STOPPING)
        if self._proc.state() == QProcess.ProcessState.Running:
            self._proc.terminate()
        # Starting 时 terminate 可能无效，_on_started 会补发停止请求。
        self._kill_timer.start()

    def restart(self, params: ServerParams) -> bool:
        if self._closing:
            return False
        params = replace(params)
        errors = params.validate()
        if errors:
            self.start_failed.emit("；".join(errors))
            return False
        if self._state == STATE_STOPPED:
            return self.start(params)
        self._pending_restart = params
        self._stop_process()
        return True

    def wait_stopped(self, timeout_ms: int = 6000) -> bool:
        """仅供非 UI 调用者使用；窗口退出通过事件循环异步等待。"""
        return self._proc is None or self._proc.waitForFinished(timeout_ms)

    def shutdown(self) -> None:
        self._closing = True
        self.stop()

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.pid_changed.emit(self.pid())
            self.state_changed.emit(state)

    def _read_output(self) -> None:
        self._buf += bytes(self._proc.readAllStandardOutput()).decode(
            "utf-8", errors="replace")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.rstrip("\r"):
                self.log_line.emit(line.rstrip("\r"))

    def _on_ready_read(self) -> None:
        if self.sender() is self._proc:
            self._read_output()

    def _on_started(self) -> None:
        if self.sender() is not self._proc:
            return
        self.pid_changed.emit(self.pid())
        if self._state == STATE_STOPPING:
            self._proc.terminate()
        elif self._state == STATE_STARTING:
            self.process_started.emit(self._generation, self.launch_params)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if self.sender() is not self._proc:
            return
        if error == QProcess.ProcessError.FailedToStart:
            message = f"llama-server 无法执行：{self._proc.errorString()}"
            self.log_line.emit(f"[!] {message}")
            self._release_process()
            self.start_failed.emit(message)
        # Crashed 随后的 finished 会统一回收并报告退出原因。

    def _on_finished(self, exit_code: int, _status) -> None:
        if self.sender() is not self._proc:
            return
        unexpected = not self._intentional_stop
        self._read_output()
        if self._buf.strip():
            self.log_line.emit(self._buf.strip())
        if unexpected:
            self.log_line.emit(f"[!] llama-server 意外退出，exit code = {exit_code}")
        else:
            self.log_line.emit(f"[*] llama-server 已退出，exit code = {exit_code}")
        self._release_process()
        if unexpected:
            self.exited_unexpectedly.emit(exit_code)

    def _release_process(self) -> None:
        self._kill_timer.stop()
        proc = self._proc
        self._proc = self._kill_timer = None
        self._buf = ""
        proc.deleteLater()
        self._set_state(STATE_STOPPED)
        pending = self._pending_restart
        self._pending_restart = None
        if pending is not None and not self._closing:
            self.start(pending)

    def _force_kill(self) -> None:
        # 每轮独立的计时器，旧轮的迟到超时不能杀掉新进程。
        if (self.sender() is self._kill_timer and self._proc is not None
                and self._proc.state() != QProcess.ProcessState.NotRunning):
            self.log_line.emit("[*] 进程未响应 terminate，执行强制结束")
            self._proc.kill()
