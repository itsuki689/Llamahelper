"""Regression coverage for the remaining code-review findings."""
from pathlib import Path
from types import SimpleNamespace
import wave
import socket
import json
import subprocess
import sys
from contextlib import nullcontext

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent

from benchmark.audio import audio_duration
from benchmark.runner import BenchJob, BenchmarkRunner
from core.params import ServerParams, _split_windows_args
from config.app_config import AppConfig
from config.presets import PresetManager
from ui.pages.parameters import ParametersPage
from core.server_manager import port_in_use
from core.api_health import ApiHealthChecker
from monitoring.system_monitor import SystemMonitor
from widget.api_client import ChatWorker
from widget.dockable_translate_widget import TranslateWidget
from widget.settings import WidgetSettings


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("content", [b"not RIFF", b"", b"RIFF\x20\0\0\0WAVE"])
def test_invalid_wav_is_reported_as_audio_error(tmp_path, content):
    path = tmp_path / "broken.wav"
    path.write_bytes(content)
    with pytest.raises(ValueError, match="broken.wav"):
        audio_duration(path)


def test_asr_skips_broken_wav_and_measures_remaining_files(qapp, tmp_path, monkeypatch):
    (tmp_path / "a.wav").write_bytes(b"bad header")
    with wave.open(str(tmp_path / "b.wav"), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 16000)
    runner = BenchmarkRunner(BenchJob(ServerParams(), mode="asr", audio_dir=str(tmp_path)))
    monkeypatch.setattr(runner, "_sample_peaks", lambda *args: None)
    monkeypatch.setattr(runner, "_post_audio", lambda *args, **kwargs: None)
    result = {"error": "", "label": "test"}
    runner._measure_asr(SimpleNamespace(poll=lambda: None), ServerParams(), result)
    assert result["ok"] and result["asr_files"] == 1
    assert result["audio_s"] == 1.0


def test_incomplete_extra_arguments_can_be_restored_and_corrected(qapp, tmp_path):
    config = AppConfig(tmp_path / "config.json")
    params = ServerParams(llama_server_path=__file__, model_path=__file__,
                          extra_args='--alias "')
    config.save_params(params)
    page = ParametersPage()
    try:
        page.set_params(config.load_params())
        assert "额外参数格式错误" in page.cmd_preview.toPlainText()
        assert any("额外参数" in error for error in page.get_params().validate())
        page.edit_extra.setText('--alias "my model"')
        assert "额外参数格式错误" not in page.cmd_preview.toPlainText()
        assert not page.get_params().validate()
    finally:
        page.close()


@pytest.mark.parametrize(("host", "expected"), [
    ("0.0.0.0", "127.0.0.1"), ("::", "[::1]"),
    ("0:0:0:0:0:0:0:0", "[::1]"), ("::1", "[::1]"),
    ("[::1]", "[::1]"), ("localhost", "localhost"),
])
def test_bind_address_is_normalized_only_for_clients(qapp, host, expected):
    params = ServerParams(host=host, port=12345)
    assert params.api_base_url() == f"http://{expected}:12345"
    assert params.web_ui_url() == params.api_base_url() + "/"
    assert ApiHealthChecker(host, 12345)._url == params.api_base_url() + "/health"
    command = params.build_command()
    assert command[command.index("--host") + 1] == host


def test_wildcard_port_probe_detects_listening_server():
    with socket.socket() as listener:
        listener.bind(("0.0.0.0", 0))
        listener.listen(4)
        assert port_in_use("0.0.0.0", listener.getsockname()[1])


def test_cpu_sampling_keeps_history_and_resets_on_pid_change(qapp, monkeypatch):
    created = []

    class Process:
        def __init__(self, pid):
            self.pid = pid
            self.calls = 0
            created.append(pid)

        def is_running(self):
            return True

        def oneshot(self):
            return nullcontext()

        def cpu_percent(self, interval=None):
            self.calls += 1
            return 0.0 if self.calls == 1 else 42.0

        def memory_info(self):
            return SimpleNamespace(rss=1024)

    monkeypatch.setattr("monitoring.system_monitor.psutil.Process", Process)
    monitor = SystemMonitor(interval=0)
    monitor.set_server_pid(11)
    samples = []

    def sample(data):
        samples.append(data["proc_cpu_percent"])
        if len(samples) == 2:
            monitor.set_server_pid(22)
        if len(samples) == 4:
            monitor.stop()

    monitor.stats.connect(sample)
    monitor.run()
    assert samples == [0.0, 42.0, 0.0, 42.0]
    assert created == [11, 22]


@pytest.mark.parametrize("data", [[], [1], None, True, 42, "text"])
def test_config_and_presets_recover_from_non_object_json(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert AppConfig(path).load_params() == ServerParams()
    assert PresetManager(path).names()


def test_bad_parameter_types_are_defaulted_without_losing_valid_fields(qapp, tmp_path):
    path = tmp_path / "config.json"
    data = {"threads": None, "model_path": [], "flash_attention": "false",
            "port": True, "context_size": 2 ** 80, "temp": float("nan"),
            "cache_type_v": "invalid", "extra_args": {}, "batch_size": 512}
    path.write_text(json.dumps({"params": data}), encoding="utf-8")
    loaded = AppConfig(path).load_params()
    assert loaded == ServerParams(batch_size=512)
    page = ParametersPage()
    try:
        page.set_params(loaded)
    finally:
        page.close()
    assert ServerParams.from_dict([1]) == ServerParams()


def test_invalid_encoding_recovers_without_crashing(tmp_path):
    path = tmp_path / "config.json"
    path.write_bytes(b"\xff\xfe\x80")
    assert AppConfig(path).load_params() == ServerParams()
    assert PresetManager(path).names()


def test_windows_equals_path_and_legacy_single_quotes():
    assert _split_windows_args(r'--chat-template-file="C:\my folder\template.jinja"') == [
        r'--chat-template-file=C:\my folder\template.jinja']
    assert _split_windows_args("--alias 'my model'") == ["--alias", "my model"]
    with pytest.raises(ValueError):
        _split_windows_args('--alias="unfinished')


@pytest.mark.parametrize("args", [
    ["--alias", "", "my model"],
    ["--alias", 'a "quoted" name', r"C:\my folder\\"],
    [r"--file=C:\my folder\template.jinja", r"\\server\share\name"],
    ["中文 名称", "a\tb", 'a\\"b', 'a\\\\"b'],
])
def test_windows_arguments_roundtrip_through_process_quoting(args):
    assert _split_windows_args(subprocess.list2cmdline(args)) == args


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch launcher")
def test_launcher_only_install_reaches_venv_creation(tmp_path):
    source = (Path(__file__).resolve().parents[1] / "start.bat").read_text(encoding="utf-8")
    # Mock discovery and venv creation; execute the real selection branch.
    prefix = source[:source.index("\n:find_python_312")]
    prefix = prefix.replace('py -3.12 -m venv "%VENV_DIR%"',
                            'echo LAUNCHER_SELECTED\n    exit /b 0')
    discovery = ('\n:find_python_312\nset "USE_PY_LAUNCHER=1"\nexit /b 0\n'
                 '\n:find_compatible_python\nexit /b 0\n')
    script = tmp_path / "start.bat"
    script.write_text(prefix + discovery + source[source.index("\n:failed"):], encoding="utf-8")
    result = subprocess.run(["cmd.exe", "/d", "/c", str(script)], cwd=tmp_path,
                            stdin=subprocess.DEVNULL, capture_output=True,
                            timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout.decode(errors="replace")
    assert b"LAUNCHER_SELECTED" in result.stdout


def test_completed_translation_workers_are_released(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ChatWorker, "_request", lambda self, image_retry: "译文")
    widget = TranslateWidget("http://127.0.0.1:1/v1", WidgetSettings(tmp_path / "widget.json"))
    try:
        for _ in range(8):
            assert widget.translate("hello")
            assert widget._worker.wait(1000)
            QApplication.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert widget._worker is None
            assert not widget.findChildren(ChatWorker)
    finally:
        widget.close()
