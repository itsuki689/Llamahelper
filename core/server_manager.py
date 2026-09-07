"""基于 QProcess 的 llama-server 进程管理。

- 不使用 shell=True，参数以列表传递，支持带空格路径
- 非阻塞：启动/停止/日志读取全部走 Qt 信号
- 防重复启动、端口占用检测、意外退出安全处理
"""
from __future__ import annotations

import socket
from dataclasses import replace

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from core.params import ServerParams

STATE_STOPPED = "stopped"
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_STOPPING = "stopping"


def port_in_use(host: str, port: int, timeout: float = 0.4) -> bool:
    """检测 TCP 端口是否已被占用。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class ServerManager(QObject):
    """包装 QProcess，向 UI 提供启动/停止/重启与日志信号。"""

    log_line = Signal(str)              # 输出行（stdout/stderr 合并）
    state_changed = Signal(str)         # STATE_*
    pid_changed = Signal(int)           # 0 表示无进程
    start_failed = Signal(str)          # 启动失败原因
    exited_unexpectedly = Signal(int)   # 运行中意外退出（exit code）
    process_started = Signal(object)    # 本次实际启动的 ServerParams 快照

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        self._proc.setProcessEnvironment(env)

        self._state = STATE_STOPPED
        self._buf = ""
        self._intentional_stop = False
        self._launch_params: ServerParams | None = None

        self._proc.readyReadStandardOutput.connect(self._on_ready_read)
        self._proc.started.connect(self._on_started)
        self._proc.errorOccurred.connect(self._on_error)
        self._proc.finished.connect(self._on_finished)

        # terminate 后若仍未退出则强杀
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.setInterval(4000)
        self._kill_timer.timeout.connect(self._force_kill)

    # ------------------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    def is_running(self) -> bool:
        return self._state in (STATE_STARTING, STATE_RUNNING)

    def pid(self) -> int:
        if self._proc.processId() and self.is_running():
            return int(self._proc.processId())
        return 0

    # ------------------------------------------------------------------
    def start(self, params: ServerParams) -> bool:
        """启动 llama-server。返回 False 表示未执行（原因已通过信号/校验给出）。"""
        if self._state != STATE_STOPPED:
            self.start_failed.emit("服务已在运行，拒绝重复启动")
            return False
        errors = params.validate()
        if errors:
            self.start_failed.emit("；".join(errors))
            return False
        if port_in_use(params.host, params.port):
            self.start_failed.emit(
                f"端口 {params.port} 已被占用，请更换端口或先停止占用进程")
            return False

        cmd = params.build_command()
        self._intentional_stop = False
        self._buf = ""
        self._launch_params = replace(params)
        self._set_state(STATE_STARTING)
        self.log_line.emit("$ " + params.command_display())
        self._proc.start(cmd[0], cmd[1:])
        # errorOccurred / started 由信号驱动；启动成功后在 _on_ready_read 见到输出
        # 但进程可能启动后立刻就绪，这里保守地：只要 start 没报错就视为 starting，
        # 由 API 健康检查（UI 侧）确认 running。此处直接进入 running 由
        # mark_running() 显式触发，避免误判。
        return True

    def mark_running(self) -> None:
        """由 UI 在 API 健康检查通过后调用，标记服务可用。"""
        if self._state == STATE_STARTING:
            self._set_state(STATE_RUNNING)

    def stop(self) -> None:
        if not self.is_running():
            return
        self._intentional_stop = True
        self._set_state(STATE_STOPPING)
        self._proc.terminate()
        self._kill_timer.start()

    def restart(self, params: ServerParams) -> None:
        """停止后自动以 params 重新启动。"""
        if self._state != STATE_STOPPED:
            self._pending_restart = replace(params)
            if self.is_running():
                self.stop()
        else:
            self.start(params)

    def wait_stopped(self, timeout_ms: int = 6000) -> bool:
        """阻塞等待进程退出（仅用于应用关闭场景）。"""
        if self._proc.state() == QProcess.ProcessState.NotRunning:
            return True
        return self._proc.waitForFinished(timeout_ms)

    def shutdown(self) -> None:
        """应用退出：取消已排队的重启，并异步停止进程。"""
        self._pending_restart = None
        self.stop()

    # ------------------------------------------------------------------
    def _set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self.pid_changed.emit(self.pid())
        self.state_changed.emit(state)

    def _on_ready_read(self) -> None:
        data = bytes(self._proc.readAllStandardOutput())
        text = data.decode("utf-8", errors="replace")
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip("\r")
            if line:
                self.log_line.emit(line)

    def _on_started(self) -> None:
        if self._state == STATE_STARTING and self._launch_params is not None:
            self.pid_changed.emit(self.pid())
            self.process_started.emit(replace(self._launch_params))

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._set_state(STATE_STOPPED)
            self.start_failed.emit("llama-server 启动失败：无法执行文件或权限不足")
        elif error == QProcess.ProcessError.Crashed and self._state in (
                STATE_STARTING, STATE_RUNNING):
            # finished 信号随后会处理状态切换与 exited_unexpectedly
            pass

    def _on_finished(self, exit_code: int, _status) -> None:
        self._kill_timer.stop()
        if self._buf.strip():
            self.log_line.emit(self._buf.strip())
            self._buf = ""
        was_unexpected = (not self._intentional_stop
                          and self._state in (STATE_STARTING, STATE_RUNNING))
        self._set_state(STATE_STOPPED)
        if was_unexpected:
            self.log_line.emit(f"[!] llama-server 意外退出，exit code = {exit_code}")
            self.exited_unexpectedly.emit(exit_code)
        else:
            self.log_line.emit(f"[*] llama-server 已退出，exit code = {exit_code}")
        pending = getattr(self, "_pending_restart", None)
        self._pending_restart = None
        if pending is not None:
            self.start(pending)

    def _force_kill(self) -> None:
        if self._proc.state() != QProcess.ProcessState.NotRunning:
            self.log_line.emit("[*] 进程未响应 terminate，执行强制结束")
            self._proc.kill()
