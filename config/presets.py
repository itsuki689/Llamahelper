"""预设管理：多个命名参数集，保存到程序目录 presets.json。"""
from __future__ import annotations

import json
from pathlib import Path

from config.app_config import app_dir
from core.params import ServerParams

PRESETS_FILE_NAME = "presets.json"

# 针对 5070 Ti Laptop 12GB + 9955HX + 64GB 的默认预设（Qwen MoE 向）
DEFAULT_PRESETS: dict[str, dict] = {
    "Qwen35B Balanced": {
        "context_size": 16384, "threads": 16, "batch_size": 1024,
        "gpu_layers_auto": True, "flash_attention": True,
        "cache_type_k": "q8_0", "cache_type_v": "q8_0",
        "cpu_moe": False, "n_cpu_moe": 32,
    },
    "Qwen35B Max Speed": {
        "context_size": 8192, "threads": 16, "batch_size": 2048,
        "gpu_layers_auto": True, "flash_attention": True,
        "cache_type_k": "q8_0", "cache_type_v": "q8_0",
        "cpu_moe": False, "n_cpu_moe": 16,
    },
    "Long Context": {
        "context_size": 65536, "threads": 16, "batch_size": 512,
        "gpu_layers_auto": True, "flash_attention": True,
        "cache_type_k": "q8_0", "cache_type_v": "q4_0",
        "cpu_moe": False, "n_cpu_moe": 48,
    },
    "Low VRAM": {
        "context_size": 8192, "threads": 16, "batch_size": 512,
        "gpu_layers_auto": False, "gpu_layers": 20, "flash_attention": True,
        "cache_type_k": "q8_0", "cache_type_v": "q4_0",
        "cpu_moe": False, "n_cpu_moe": 48,
    },
}


class PresetManager:
    """presets.json 的加载、保存、增删。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (app_dir() / PRESETS_FILE_NAME)
        self._presets: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
            self._presets = {str(k): dict(v) for k, v in data.items()
                             if isinstance(v, dict)}
        except (OSError, UnicodeError, json.JSONDecodeError):
            self._presets = {}
        if not self._presets:
            self._presets = {k: dict(v) for k, v in DEFAULT_PRESETS.items()}
            self.save()

    def save(self) -> None:
        try:
            self.path.write_text(
                json.dumps(self._presets, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError:
            pass

    def names(self) -> list[str]:
        return list(self._presets.keys())

    def get(self, name: str) -> dict | None:
        return self._presets.get(name)

    def put(self, name: str, params: ServerParams) -> None:
        self._presets[name] = params.preset_dict()
        self.save()

    def delete(self, name: str) -> None:
        if name in self._presets:
            del self._presets[name]
            self.save()

    def apply_to(self, name: str, params: ServerParams) -> ServerParams:
        """把预设字段合并进 params（保留 exe / 模型路径）。"""
        data = self._presets.get(name)
        if data:
            merged = params.to_dict()
            merged.update(data)
            params = ServerParams.from_dict(merged)
        return params
