"""llama-server 启动参数定义与命令行构建。"""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field, asdict
from pathlib import Path

# KV cache 可选类型（llama.cpp 支持值）
KV_CACHE_TYPES = ["f16", "bf16", "q8_0", "q5_1", "q5_0", "q4_1", "q4_0", "iq4_nl"]

# GPU Layers 为 Auto 时传给 -ngl 的值（足够大即全部 offload）
GPU_LAYERS_AUTO_VALUE = 999


def split_extra_args(text: str) -> list[str]:
    """把用户输入的额外参数串拆成参数列表，兼容 Windows 路径反斜杠与引号。"""
    text = text.strip()
    if not text:
        return []
    # posix=False 保留 Windows 路径中的反斜杠，但会保留引号，需要再剥掉
    parts = shlex.split(text, posix=(os.name != "nt"))
    if os.name == "nt":
        cleaned = []
        for p in parts:
            if len(p) >= 2 and p[0] == p[-1] and p[0] in ("'", '"'):
                p = p[1:-1]
            cleaned.append(p)
        parts = cleaned
    return parts


@dataclass
class ServerParams:
    """一次 llama-server 启动所需的全部参数。"""

    llama_server_path: str = ""
    model_path: str = ""

    context_size: int = 8192
    threads: int = 16
    batch_size: int = 1024

    gpu_layers_auto: bool = True
    gpu_layers: int = 99

    flash_attention: bool = True
    cache_type_k: str = "f16"
    cache_type_v: str = "f16"

    cpu_moe: bool = False        # --cpu-moe：全部 MoE 权重放 CPU
    n_cpu_moe: int = 0           # --n-cpu-moe N：前 N 层 MoE 放 CPU（0 = 不启用）

    host: str = "127.0.0.1"
    port: int = 8080
    alias: str = ""

    extra_args: str = ""

    # ------------------------------------------------------------------
    def build_command(self) -> list[str]:
        """生成 QProcess/Popen 用的参数列表（不使用 shell，路径可含空格）。"""
        cmd: list[str] = [str(Path(self.llama_server_path))]
        cmd += ["-m", str(Path(self.model_path))]
        cmd += ["-c", str(self.context_size)]
        cmd += ["-t", str(self.threads)]
        cmd += ["-b", str(self.batch_size)]
        cmd += ["-ngl", str(GPU_LAYERS_AUTO_VALUE if self.gpu_layers_auto else self.gpu_layers)]
        # b10453 等新版本 --flash-attn 需要取值 auto|on|off
        cmd += ["--flash-attn", "on" if self.flash_attention else "off"]
        cmd += ["--cache-type-k", self.cache_type_k]
        cmd += ["--cache-type-v", self.cache_type_v]
        if self.cpu_moe:
            cmd += ["--cpu-moe"]
        elif self.n_cpu_moe > 0:
            cmd += ["--n-cpu-moe", str(self.n_cpu_moe)]
        cmd += ["--host", self.host]
        cmd += ["--port", str(self.port)]
        if self.alias.strip():
            cmd += ["--alias", self.alias.strip()]
        cmd += split_extra_args(self.extra_args)
        return cmd

    def command_display(self) -> str:
        """用于界面展示的命令字符串（含必要引号）。"""
        parts = []
        for arg in self.build_command():
            if " " in arg:
                parts.append(f'"{arg}"')
            else:
                parts.append(arg)
        return " ".join(parts)

    def api_base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def web_ui_url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    # ------------------------------------------------------------------
    def validate(self) -> list[str]:
        """返回错误信息列表，空列表表示可启动。"""
        errors: list[str] = []
        exe = Path(self.llama_server_path) if self.llama_server_path else None
        if not exe or not exe.is_file():
            errors.append("未选择有效的 llama-server 可执行文件")
        model = Path(self.model_path) if self.model_path else None
        if not model or not model.is_file():
            errors.append("未选择有效的 GGUF 模型文件")
        if not self.host.strip():
            errors.append("Host 不能为空")
        if not (1 <= self.port <= 65535):
            errors.append("端口必须在 1-65535 之间")
        if self.cache_type_k not in KV_CACHE_TYPES:
            errors.append(f"非法 KV Cache K 类型: {self.cache_type_k}")
        if self.cache_type_v not in KV_CACHE_TYPES:
            errors.append(f"非法 KV Cache V 类型: {self.cache_type_v}")
        return errors

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ServerParams":
        names = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        filtered = {k: v for k, v in (data or {}).items() if k in names}
        return cls(**filtered)

    def preset_dict(self) -> dict:
        """预设只保存调参字段，不含 exe / 模型路径。"""
        d = self.to_dict()
        d.pop("llama_server_path", None)
        d.pop("model_path", None)
        return d
