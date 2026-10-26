"""ASR Benchmark（RTF 模式）测试：音频时长解析 + 端到端一轮测量。"""
from __future__ import annotations

import math
import socket
import struct
import wave
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from benchmark.audio import audio_duration, list_audio_files
from benchmark.runner import BenchJob, BenchmarkRunner
from core.params import ServerParams
from core.server_manager import port_in_use

FAKE_SERVER = Path(__file__).parent / "fake_llama_server.py"


def make_wav(path: Path, seconds: float = 1.0, rate: int = 16000) -> None:
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * frames)


def test_wav_duration(tmp_path):
    f = tmp_path / "a.wav"
    make_wav(f, seconds=2.5)
    assert abs(audio_duration(f) - 2.5) < 0.01


def test_flac_duration(tmp_path):
    # 手工构造最小合法 FLAC 头：fLaC + STREAMINFO(34B)
    rate, total = 44100, 44100 * 3  # 3 秒
    x = (rate << 44) | total
    info = bytearray(34)
    info[10:18] = x.to_bytes(8, "big")
    f = tmp_path / "b.flac"
    f.write_bytes(b"fLaC" + bytes([0x00, 0x00, 0x00, 34]) + bytes(info))
    assert abs(audio_duration(f) - 3.0) < 0.01


def make_mp3(path: Path, frames: int = 100, id3: bool = False) -> float:
    """手工构造 MPEG1 Layer3 128kbps 44100Hz 的 MP3，返回预期时长（秒）。"""
    header = bytes([0xFF, 0xFB, 0x90, 0x00])   # sync/V1/L3, 128k idx9, 44.1k idx0
    frame_len = 144 * 128000 // 44100          # 417 字节
    body = header + b"\x00" * (frame_len - 4)
    data = body * frames
    if id3:
        # ID3v2 标签：syncsafe size=20，标签体里故意放假同步头干扰
        tag = b"ID3" + bytes([4, 0, 0, 0, 0, 0, 20])
        data = tag + bytes([0xFF, 0xFB, 0x90, 0x00]) + b"\x00" * 16 + data
    path.write_bytes(data)
    return frames * 1152 / 44100


def test_mp3_duration(tmp_path):
    f = tmp_path / "c.mp3"
    expected = make_mp3(f, frames=100)
    assert abs(audio_duration(f) - expected) < 0.01


def test_mp3_duration_with_id3(tmp_path):
    f = tmp_path / "d.mp3"
    expected = make_mp3(f, frames=50, id3=True)
    assert abs(audio_duration(f) - expected) < 0.01


def test_unsupported_suffix(tmp_path):
    f = tmp_path / "c.ogg"
    f.write_bytes(b"\x00" * 64)
    with pytest.raises(ValueError):
        audio_duration(f)


def test_invalid_mp3_raises(tmp_path):
    f = tmp_path / "broken.mp3"
    f.write_bytes(b"\x00" * 64)
    with pytest.raises(ValueError):
        audio_duration(f)


def test_list_audio_files(tmp_path):
    make_wav(tmp_path / "b.wav")
    make_wav(tmp_path / "a.wav")
    make_mp3(tmp_path / "c.mp3", frames=1)
    (tmp_path / "note.txt").write_text("x")
    files = list_audio_files(tmp_path)
    assert [f.name for f in files] == ["a.wav", "b.wav", "c.mp3"]
    assert list_audio_files(tmp_path / "nope") == []


# ---------------------------------------------------------------------
def test_benchmark_asr_one_round(fake_exe, tmp_path):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    make_wav(audio_dir / "s1.wav", seconds=1.0)
    make_wav(audio_dir / "s2.wav", seconds=2.0)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    job = BenchJob(
        base_params=ServerParams(
            llama_server_path=str(fake_exe),
            model_path=str(FAKE_SERVER),
            host="127.0.0.1", port=port),
        mode="asr",
        sweep_field="n_cpu_moe",
        sweep_start=16, sweep_end=16, sweep_step=8,
        include_cpu_moe=False,
        audio_dir=str(audio_dir),
        health_timeout=30.0,
    )
    runner = BenchmarkRunner(job)
    results: list[dict] = []
    runner.sig_run_result.connect(results.append)
    runner.run()

    assert len(results) == 1
    r = results[0]
    assert r["ok"], f"ASR benchmark 失败: {r['error']}"
    assert r["asr_files"] == 2
    assert abs(r["audio_s"] - 3.0) < 0.01   # 1s + 2s
    assert r["infer_s"] > 0
    assert r["rtf"] > 0
    assert math.isclose(r["rtf"], r["audio_s"] / r["infer_s"], rel_tol=1e-6)
    assert not port_in_use("127.0.0.1", port)


def test_benchmark_asr_empty_dir(fake_exe, tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    job = BenchJob(
        base_params=ServerParams(
            llama_server_path=str(fake_exe),
            model_path=str(FAKE_SERVER),
            host="127.0.0.1", port=port),
        mode="asr", audio_dir=str(tmp_path / "empty"),
        sweep_field="n_cpu_moe", sweep_start=8, sweep_end=8,
        include_cpu_moe=False,
    )
    runner = BenchmarkRunner(job)
    results: list[dict] = []
    finished: list[bool] = []
    runner.sig_run_result.connect(results.append)
    runner.sig_finished_all.connect(lambda: finished.append(True))
    runner.run()
    assert finished and not results  # 目录为空直接结束，不产生结果
