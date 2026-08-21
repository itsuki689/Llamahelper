"""ASR Benchmark 的音频工具：时长读取与目录扫描（零第三方依赖）。

支持 .wav（stdlib wave）、.flac（解析 STREAMINFO 元数据块）与
.mp3（扫描 MPEG 帧头逐帧累加时长，CBR/VBR 均可）。
"""
from __future__ import annotations

import wave
from pathlib import Path

SUPPORTED_AUDIO_SUFFIXES = (".wav", ".flac", ".mp3")


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


# MPEG 帧头表：版本 bits 3=MPEG1 2=MPEG2 0=MPEG2.5，层 bits 1=L3 2=L2 3=L1
_MP3_BITRATES = {  # kbps，(版本归一到 3/2, 层) 索引
    (3, 3): [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, 0],
    (3, 2): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 0],
    (3, 1): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0],
    (2, 3): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256, 0],
    (2, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
    (2, 1): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
}
_MP3_SAMPLE_RATES = {
    3: [44100, 48000, 32000],
    2: [22050, 24000, 16000],
    0: [11025, 12000, 8000],
}


def _mp3_frame(header: int):
    """解析 4 字节帧头，返回 (帧长字节, 每帧采样数, 采样率)，无效返回 None。"""
    version = (header >> 19) & 0x3      # 0=2.5 1=保留 2=2 3=1
    layer = (header >> 17) & 0x3        # 1=L3 2=L2 3=L1
    br_idx = (header >> 12) & 0xF
    sr_idx = (header >> 10) & 0x3
    padding = (header >> 9) & 0x1
    if version == 1 or layer == 0 or br_idx in (0, 15) or sr_idx == 3:
        return None
    br = _MP3_BITRATES[(3 if version == 3 else 2, layer)][br_idx] * 1000
    sr = _MP3_SAMPLE_RATES[version][sr_idx]
    if layer == 3:                      # Layer I
        return (12 * br // sr + padding) * 4, 384, sr
    if layer == 2:                      # Layer II
        return 144 * br // sr + padding, 1152, sr
    # Layer III：MPEG1 每帧 1152 采样，MPEG2/2.5 每帧 576 采样
    coeff, samples = (144, 1152) if version == 3 else (72, 576)
    return coeff * br // sr + padding, samples, sr


def mp3_duration(path: Path) -> float:
    """扫描 MP3 帧头逐帧累加时长（跳过 ID3v2 标签；CBR/VBR 均可）。"""
    data = path.read_bytes()
    if len(data) < 4:
        raise ValueError(f"MP3 文件过短: {path}")
    i = 0
    if data[:3] == b"ID3" and len(data) >= 10:  # ID3v2：4 字节 syncsafe 大小
        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        i = 10 + size + (10 if data[5] & 0x10 else 0)  # footer 标志再 +10
    total = 0.0
    frames = 0
    n = len(data)
    while i + 4 <= n:
        if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
            header = int.from_bytes(data[i:i + 4], "big")
            frame = _mp3_frame(header)
            if frame is not None:
                frame_len, samples, sr = frame
                total += samples / sr
                frames += 1
                i += frame_len
                continue
        i += 1
    if frames == 0:
        raise ValueError(f"MP3 中未找到有效音频帧: {path}")
    return total


def audio_duration(path: Path) -> float:
    """返回音频时长（秒），不支持的格式抛 ValueError。"""
    suffix = path.suffix.lower()
    if suffix == ".wav":
        return wav_duration(path)
    if suffix == ".flac":
        return flac_duration(path)
    if suffix == ".mp3":
        return mp3_duration(path)
    raise ValueError(f"不支持获取时长的格式: {suffix}（仅支持 wav/flac/mp3）")


def list_audio_files(directory: str | Path) -> list[Path]:
    """列出目录下支持的音频文件（按文件名排序，保证测试顺序稳定）。"""
    d = Path(directory)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir()
                  if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO_SUFFIXES)
