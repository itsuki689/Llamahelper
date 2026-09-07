"""Regression coverage for the remaining code-review findings."""
from pathlib import Path
from types import SimpleNamespace
import wave
import socket

import pytest
from PySide6.QtWidgets import QApplication

from benchmark.audio import audio_duration
from benchmark.runner import BenchJob, BenchmarkRunner
from core.params import ServerParams
from config.app_config import AppConfig
from ui.pages.parameters import ParametersPage
from core.server_manager import port_in_use
from ui.main_window import ApiHealthChecker


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
