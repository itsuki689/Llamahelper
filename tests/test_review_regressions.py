"""Regression coverage for the remaining code-review findings."""
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest
from PySide6.QtWidgets import QApplication

from benchmark.audio import audio_duration
from benchmark.runner import BenchJob, BenchmarkRunner
from core.params import ServerParams


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
