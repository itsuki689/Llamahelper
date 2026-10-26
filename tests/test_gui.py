"""GUI smoke 测试：offscreen 平台下验证主窗口、参数生成、配置保存。"""
from __future__ import annotations

import csv
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
    deadline = time.monotonic() + 6
    while win._close_timer.isActive() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not win._close_timer.isActive()
    app.processEvents()


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_app_icon_exists_and_loads(qapp):
    from PySide6.QtGui import QIcon

    from main import load_app_icon, resource_path
    for name in ("assets/icon.png", "assets/icon.ico"):
        icon_path = resource_path(name)
        assert icon_path.is_file()
        assert not QIcon(str(icon_path)).isNull()
    assert not load_app_icon().isNull()


def test_main_window_is_frameless_and_keeps_icon(window, qapp):
    from PySide6.QtCore import Qt
    from main import load_app_icon

    window.setWindowIcon(load_app_icon())
    flags = window.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert not flags & Qt.WindowType.WindowTitleHint
    assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert window.windowTitle().startswith("LlamaHelper")
    assert not window.windowIcon().isNull()
    assert window.btn_win_minimize.property("role") == "minimize"
    assert window.btn_win_maximize.property("role") == "maximize"
    assert window.btn_win_close.property("role") == "close"
    assert window.btn_win_minimize.text() == "\u2212"
    assert window.btn_win_maximize.text() == "\u25a1"
    assert window.btn_win_close.text() == "\u00d7"
    assert window.btn_win_close.size().width() == 46


def test_frameless_window_maximize_toggle(window, qapp):
    window.showMaximized()
    qapp.processEvents()
    assert window.btn_win_maximize.text() == "\u2750"
    window._toggle_maximized()
    qapp.processEvents()
    assert not window.isMaximized()
    assert window.btn_win_maximize.text() == "\u25a1"


def test_main_window_opens_dockable_translation_widget(window, qapp):
    from widget.dockable_translate_widget import (
        MODE_DOCKED, MODE_IDLE, TranslateWidget,
    )

    window.open_translate_settings()
    window.translation_widget_page.btn_launch.click()
    qapp.processEvents()
    assert isinstance(window._translate_widget, TranslateWidget)
    assert window._translate_widget.mode in {MODE_IDLE, MODE_DOCKED}
    window._translate_widget.close()
    qapp.processEvents()
    assert window._translate_widget is None


def test_main_window_opens_translation_settings(
        window, qapp):
    window.open_translate_settings()
    qapp.processEvents()
    page = window.translation_widget_page
    assert window.stack.currentWidget() is page
    assert window.nav_buttons[5].isChecked()
    assert page.preset_list.count() >= 1
    assert page.btn_launch.text() == "启动挂件"


def test_main_window_pages(window):
    assert window.stack.count() == 6
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
    assert "-ngl auto" in text  # GPU Layers Auto


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
    assert job.sweep_field == "n_cpu_moe"
    assert job.sweep_start == 48 and job.sweep_end == 16
    assert job.sweep_step == 8
    assert job.warmup_tokens == 32
    # 切换到非 ncmoe 扫描目标时，cpu-moe 复选框隐藏
    window.benchmark.combo_sweep.setCurrentText("GPU Layers")
    assert not window.benchmark.chk_cpu_moe.isVisible()


def test_wheel_guard(window):
    """未聚焦的 spin 不响应滚轮，聚焦策略为 StrongFocus。"""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    spin = window.parameters.spin_ctx
    assert spin.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert not spin.hasFocus()
    before = spin.value()
    ev = QWheelEvent(QPointF(5, 5), QPointF(spin.mapToGlobal(QPoint(5, 5))),
                     QPoint(0, 0), QPoint(0, 120),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(spin, ev)
    assert spin.value() == before  # 未聚焦滚轮不生效


def test_benchmark_asr_mode_ui(window, monkeypatch, tmp_path):
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *a, **k: None)
    bp = window.benchmark
    assert bp._current_mode() == "gen"
    assert bp.prompt_box.isVisibleTo(bp)
    assert not bp.audio_box.isVisibleTo(bp)
    bp.combo_mode.setCurrentText("ASR 语音识别（RTFx）")
    assert bp.audio_box.isVisibleTo(bp)
    assert not bp.prompt_box.isVisibleTo(bp)
    assert not bp.tokens_box.isVisibleTo(bp)
    bp._on_run()
    from ui.pages.benchmark_page import ASR_COLUMNS
    assert bp.table.columnCount() == len(ASR_COLUMNS)
    assert bp.table.horizontalHeaderItem(2).text() == "平均 RTFx"
    assert "RTFx = 音频时长 / 识别耗时（越大越快）" in bp._audio_hint.text()

    for value, rtfx in ((16, 4.0), (32, 8.0)):
        bp.on_run_result({
            "label": f"n_cpu_moe={value}", "field": "n_cpu_moe", "value": value,
            "gen_tps": 0.0, "prompt_tps": 0.0, "rtfx": rtfx,
            "audio_s": 12.0, "infer_s": 12.0 / rtfx, "asr_files": 1,
            "vram_peak_mb": 0.0, "power_peak_w": 0.0, "ram_peak_mb": 0.0,
            "ok": True, "status": "OK", "error": "",
        })
    # The larger inverse factor remains faster; table and completion agree.
    assert bp.table.item(0, 1).text() == "n_cpu_moe=32"
    assert bp.table.item(0, 2).text() == "8.00"
    bp.on_finished_all()
    assert "n_cpu_moe=32 @ RTFx 8.00" in bp.progress_label.text()

    exported = tmp_path / "asr.csv"
    monkeypatch.setattr("ui.pages.benchmark_page.QFileDialog.getSaveFileName",
                        lambda *args: (str(exported), "CSV 文件 (*.csv)"))
    bp._export_csv()
    with exported.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        assert "RTFx" in reader.fieldnames and "rtf" not in reader.fieldnames
        rows = list(reader)
    assert [row["RTFx"] for row in rows] == ["4.00", "8.00"]


def test_threads_batch_sweep_available():
    from benchmark.runner import SWEEP_FIELDS
    assert "Threads Batch" in SWEEP_FIELDS
    assert SWEEP_FIELDS["Threads Batch"][0] == "threads_batch"


def test_advanced_params_in_gui(window):
    p = ServerParams(
        llama_server_path="C:/llama/llama-server.exe",
        model_path="D:/m.gguf",
        mmproj_path="D:/mmproj.gguf",
        spec_mtp=True, spec_draft_n_max=4,
        ubatch_size=512, parallel=2, cont_batching=False,
        rope_scaling="yarn", temp=0.6,
    )
    window.parameters.set_params(p)
    text = window.parameters.cmd_preview.toPlainText()
    assert f"-mm {Path('D:/mmproj.gguf')}" in text
    assert "--spec-type draft-mtp" in text
    assert "--spec-draft-n-max 4" in text
    assert "-ub 512" in text
    assert "-np 2" in text
    assert "--no-cont-batching" in text
    assert "--rope-scaling yarn" in text
    assert "--temp 0.6" in text
    # 往返一致
    got = window.parameters.get_params()
    assert got.spec_mtp and got.spec_draft_n_max == 4
    assert got.ubatch_size == 512 and got.temp == 0.6
