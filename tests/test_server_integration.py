"""集成测试：用假 llama-server 验证进程启停、日志解析、端口检测与 Benchmark。"""
from __future__ import annotations

import socket
import time
import urllib.request
from pathlib import Path

import pytest

from PySide6.QtWidgets import QApplication

from benchmark.runner import BenchJob, BenchmarkRunner, build_config_list
from core.params import ServerParams
from core.server_manager import (STATE_RUNNING, STATE_STARTING, STATE_STOPPED,
                                 STATE_STOPPING, ServerManager, port_in_use)

FAKE_SERVER = Path(__file__).parent / "fake_llama_server.py"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pump_until(cond, timeout: float = 15.0) -> bool:
    app = QApplication.instance()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def make_params(fake_exe: Path, port: int, **kw) -> ServerParams:
    return ServerParams(
        llama_server_path=str(fake_exe),
        model_path=str(FAKE_SERVER),  # 仅需存在的文件占位
        host="127.0.0.1", port=port, **kw)


@pytest.fixture
def manager(qapp):
    mgr = ServerManager()
    yield mgr
    mgr.shutdown()
    assert pump_until(lambda: mgr.state == STATE_STOPPED, timeout=8)


# ---------------------------------------------------------------------
def test_port_in_use_detection():
    port = free_port()
    assert not port_in_use("127.0.0.1", port)
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))
        s.listen(1)
        assert port_in_use("127.0.0.1", port)


def test_server_start_logs_stop(manager, fake_exe):
    port = free_port()
    mgr = manager
    logs: list[str] = []
    mgr.log_line.connect(logs.append)
    states: list[str] = []
    mgr.state_changed.connect(states.append)

    assert mgr.start(make_params(fake_exe, port))
    assert mgr.state == STATE_STARTING
    assert pump_until(lambda: mgr.pid() > 0)

    # 等待假服务器输出监听日志
    assert pump_until(lambda: any("server_listen" in l for l in logs))
    # 模拟 UI 健康检查通过
    mgr.mark_running(mgr.generation)
    assert mgr.state == STATE_RUNNING

    # 防重复启动
    assert not mgr.start(make_params(fake_exe, port))

    mgr.stop()
    assert pump_until(lambda: mgr.state == STATE_STOPPED)
    assert mgr.pid() == 0
    assert STATE_STOPPING in states


def test_server_unexpected_exit_no_crash(manager, fake_exe):
    port = free_port()
    mgr = manager
    unexpected: list[int] = []
    mgr.exited_unexpectedly.connect(unexpected.append)
    assert mgr.start(make_params(fake_exe, port))
    assert pump_until(lambda: mgr.pid() > 0)
    # 直接杀掉底层进程模拟崩溃
    mgr._proc.kill()
    assert pump_until(lambda: mgr.state == STATE_STOPPED)
    assert unexpected, "意外退出应发出 exited_unexpectedly 信号"


def test_start_with_occupied_port_fails(manager, fake_exe):
    port = free_port()
    mgr = manager
    failures: list[str] = []
    mgr.start_failed.connect(failures.append)
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))
        s.listen(1)
        assert not mgr.start(make_params(fake_exe, port))
    assert failures and "占用" in failures[0]


def test_fake_server_can_exit_cleanly(manager, fake_exe):
    port = free_port()
    logs, exits = [], []
    manager.log_line.connect(logs.append)
    manager.exited_unexpectedly.connect(exits.append)
    assert manager.start(make_params(fake_exe, port))
    assert pump_until(lambda: any("server_listen" in line for line in logs))
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/shutdown", timeout=2):
        pass
    assert pump_until(lambda: manager.state == STATE_STOPPED)
    assert exits == [0]  # Even an unrequested clean exit must leave starting.
    assert "fake server: stopped" in logs
    assert not port_in_use("127.0.0.1", port)


# ---------------------------------------------------------------------
def test_build_config_list():
    job = BenchJob(base_params=ServerParams(), sweep_field="n_cpu_moe",
                   sweep_start=48, sweep_end=16, sweep_step=8,
                   include_cpu_moe=True)
    labels = [c["label"] for c in build_config_list(job)]
    assert labels == ["cpu-moe (全部MoE在CPU)", "n_cpu_moe=48", "n_cpu_moe=40",
                      "n_cpu_moe=32", "n_cpu_moe=24", "n_cpu_moe=16"]
    job2 = BenchJob(base_params=ServerParams(), sweep_field="n_cpu_moe",
                    sweep_start=8, sweep_end=32, sweep_step=8,
                    include_cpu_moe=False)
    assert [c["overrides"]["n_cpu_moe"] for c in build_config_list(job2)] == \
        [8, 16, 24, 32]


def test_build_config_list_other_fields():
    job = BenchJob(base_params=ServerParams(), sweep_field="gpu_layers",
                   sweep_start=40, sweep_end=20, sweep_step=10,
                   include_cpu_moe=False)
    cfgs = build_config_list(job)
    assert [c["overrides"]["gpu_layers"] for c in cfgs] == [40, 30, 20]
    assert all(c["overrides"]["gpu_layers_auto"] is False for c in cfgs)
    # 未知字段报错
    bad = BenchJob(base_params=ServerParams(), sweep_field="nope")
    try:
        build_config_list(bad)
        assert False, "应抛出 ValueError"
    except ValueError:
        pass


def test_benchmark_one_round(fake_exe):
    port = free_port()
    job = BenchJob(
        base_params=make_params(fake_exe, port),
        sweep_field="n_cpu_moe",
        sweep_start=32, sweep_end=32, sweep_step=8,
        gen_tokens=32, warmup_tokens=8, include_cpu_moe=False,
        prompt="Write a long story about robots. " * 20,
        health_timeout=30.0,
    )
    runner = BenchmarkRunner(job)
    results: list[dict] = []
    runner.sig_run_result.connect(results.append)
    runner.run()  # 直接同步调用（QThread.run 可在当前线程执行）

    assert len(results) == 1
    r = results[0]
    assert r["ok"], f"benchmark 失败: {r['error']}"
    assert r["gen_tps"] > 0 and r["prompt_tps"] > 0
    assert r["label"] == "n_cpu_moe=32"
    assert r["field"] == "n_cpu_moe" and r["value"] == 32
    # 进程已关闭、端口已释放
    assert not port_in_use("127.0.0.1", port)
