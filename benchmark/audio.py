"""ASR Benchmark 的音频工具：时长读取与目录扫描（零第三方依赖）。

支持 .wav（stdlib wave）与 .flac（解析 STREAMINFO 元数据块）。
"""
from __future__ import annotations

import wave
from pathlib import Path

SUPPORTED_AUDIO_SUFFIXES = (".wav", ".flac")


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate()
    if rate <= 0:
        raise ValueError(f"非法采样率: {path}")
    return frames / rate


def flac_duration(path: Path) -> float:
    """解析 FLAC STREAMINFO：采样率 20bit + 总采样数 36bit。"""
    with open(path, "rb") as f:
        if f.read(4) != b"fLaC":
            raise ValueError(f"不是 FLAC 文件: {path}")
        header = f.read(4)
        if len(header) < 4 or (header[0] & 0x7F) != 0:
            raise ValueError(f"FLAC 缺少 STREAMINFO: {path}")
        info = f.read(34)
    if len(info) < 18:
        raise ValueError(f"FLAC STREAMINFO 过短: {path}")
    x = int.from_bytes(info[10:18], "big")
    sample_rate = (x >> 44) & 0xFFFFF
    total_samples = x & 0xFFFFFFFFF
    if sample_rate <= 0 or total_samples <= 0:
        raise ValueError(f"FLAC 时长信息无效: {path}")
    return total_samples / sample_rate


def audio_duration(path: Path) -> float:
    """返回音频时长（秒），不支持的格式抛 ValueError。"""
    suffix = path.suffix.lower()
    if suffix == ".wav":
        return wav_duration(path)
    if suffix == ".flac":
        return flac_duration(path)
    raise ValueError(f"不支持获取时长的格式: {suffix}（仅支持 wav/flac）")


def list_audio_files(directory: str | Path) -> list[Path]:
    """列出目录下支持的音频文件（按文件名排序，保证测试顺序稳定）。"""
    d = Path(directory)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir()
                  if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO_SUFFIXES)
