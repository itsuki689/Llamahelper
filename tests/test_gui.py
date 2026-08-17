"""GUI smoke 测试：offscreen 平台下验证主窗口、参数生成、配置保存。"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

import ui.main_window as mw
from config.app_config import AppConfig
from config.presets import PresetManager
from core.params import ServerParams


@pytest.fixture()
def window(qapp, tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "cfg.json"
    presets_path = tmp_path / "presets.json"
    monkeypatch.setattr(mw, "AppConfig", lambda: AppConfig(cfg_path))
    monkeypatch.setattr(mw, "PresetManager", lambda: PresetManager(presets_path))
    win = mw.MainWindow()
    yield win
    win.close()
    app.processEvents()


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_pages(window):
    assert window.stack.count() == 5
    window._switch_page(2)
    assert window.stack.currentWidget() is window.benchmark
    assert window.nav_buttons[2].isChecked()
    window._switch_page(0)
    assert window.stack.currentWidget() is window.dashboard


def test_command_preview_updates(window):
    p = ServerParams(
        llama_server_path="C:/llama/llama-server.exe",
        model_path="D:/models/qwen moe/model.gguf",
        context_size=32768, threads=8, n_cpu_moe=48,
        cache_type_k="q8_0", flash_attention=True,
    )
    window.parameters.set_params(p)
    text = window.parameters.cmd_preview.toPlainText()
    assert "llama-server.exe" in text
    # 带空格路径有引号（Path 在 Windows 上为反斜杠，断言做同样转换）
    assert f'"{Path("D:/models/qwen moe/model.gguf")}"' in text
    assert "-c 32768" in text
    assert "-t 8" in text
    assert "--n-cpu-moe 48" in text
    assert "--cache-type-k q8_0" in text
    assert "-ngl 999" in text  # Auto


def test_params_roundtrip_and_config_saved(window, tmp_path):
    p = window.parameters.get_params()
    p2 = ServerParams(context_size=4096, extra_args="--no-warmup")
    window.parameters.set_params(p2)
    got = window.parameters.get_params()
    assert got.context_size == 4096
    assert got.extra_args == "--no-warmup"

    # params_changed 防抖后应触发 AppConfig.save_params
    window.app_config.save_params(got)
    assert window.app_config.path.exists()
    loaded = window.app_config.load_params()
    assert loaded.context_size == 4096


def test_presets_in_gui(window):
    names = window.preset_manager.names()
    assert "Qwen35B Balanced" in names
    assert window.presets.list.count() == len(names)
    # 应用预设
    window._apply_preset("Long Context")
    assert window.parameters.get_params().context_size == 65536
    # 保存当前为新预设
    window._save_preset("Test Preset")
    assert "Test Preset" in window.preset_manager.names()


def test_dashboard_server_state(window):
    window.dashboard.set_server_state("running", pid=1234,
                                      addr="http://127.0.0.1:8080/",
                                      api_url="http://127.0.0.1:8080/v1")
    assert window.dashboard.btn_stop.isEnabled()
    assert window.dashboard.btn_webui.isEnabled()
    assert not window.dashboard.btn_start.isEnabled()
    window.dashboard.set_server_state("stopped")
    assert window.dashboard.btn_start.isEnabled()


def test_log_highlight_and_perf(window):
    window._on_log_line(
        "llama_perf_context_print:        eval time =    1000.00 ms / "
        "  100 runs   (   10.00 ms per token,   100.00 tokens per second)")
    assert window.dashboard.gen_tps.text() == "100.00"
    window._on_log_line("ggml_cuda error: out of memory")
    QApplication.instance().processEvents()
    assert window.logs.view.document().blockCount() >= 2


def test_benchmark_controls(window, monkeypatch):
    # 未配置 exe/模型时 MainWindow 会弹警告框；offscreen 下模态框会阻塞，打补丁屏蔽
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *a, **k: None)
    window.benchmark.spin_start.setValue(48)
    window.benchmark.spin_end.setValue(16)
    window.benchmark.spin_step.setValue(8)
    captured = []
    window.benchmark.run_requested.connect(captured.append)
    window.benchmark._on_run()
    job = captured[0]
    assert job.ncmoe_start == 48 and job.ncmoe_end == 16 and job.ncmoe_step == 8
