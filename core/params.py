"""llama-server 启动参数定义与命令行构建。"""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field, asdict
from pathlib import Path

# KV cache 可选类型（llama.cpp 支持值）
KV_CACHE_TYPES = ["f16", "bf16", "q8_0", "q5_1", "q5_0", "q4_1", "q4_0", "iq4_nl"]

# GPU Layers 为 Auto 时传给 -ngl 的值（llama.cpp 原生支持 auto：按显存自动决定）
GPU_LAYERS_AUTO_VALUE = "auto"

# RoPE 缩放方式（none = 不传递该参数）
ROPE_SCALING_TYPES = ["none", "linear", "yarn"]


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
    mmproj_path: str = ""        # -mm：多模态投影文件（可选，视觉模型用）

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

    # MTP 投机解码（模型需内置 MTP 层，如 Qwen3-Next / Qwen3.5 系列）
    spec_mtp: bool = False       # --spec-type draft-mtp
    spec_draft_n_max: int = 3    # --spec-draft-n-max N（MTP 开启时生效）

    host: str = "127.0.0.1"
    port: int = 8080
    alias: str = ""

    # ---------------- 高级参数（默认值 = 不传递，由 llama-server 决定） --------
    ubatch_size: int = 0         # -ub N
    threads_batch: int = 0       # -tb N
    parallel: int = 0            # -np N（0 = auto，不传）
    cont_batching: bool = True   # False → --no-cont-batching
    no_mmap: bool = False        # --no-mmap
    mlock: bool = False          # --mlock
    no_warmup: bool = False      # --no-warmup
    cache_reuse: int = 0         # --cache-reuse N
    rope_scaling: str = "none"   # --rope-scaling（none = 不传）
    rope_freq_scale: float = 0.0  # --rope-freq-scale（0 = 不传）
    temp: float = -1.0           # --temp（-1 = 不传，下同）
    top_k: int = -1              # --top-k
    top_p: float = -1.0          # --top-p
    min_p: float = -1.0          # --min-p

    extra_args: str = ""

    # ------------------------------------------------------------------
    def build_command(self) -> list[str]:
        """生成 QProcess/Popen 用的参数列表（不使用 shell，路径可含空格）。"""
        cmd: list[str] = [str(Path(self.llama_server_path))]
        cmd += ["-m", str(Path(self.model_path))]
        if self.mmproj_path.strip():
            cmd += ["-mm", str(Path(self.mmproj_path))]
        cmd += ["-c", str(self.context_size)]
        cmd += ["-t", str(self.threads)]
        cmd += ["-b", str(self.batch_size)]
        cmd += ["-ngl", GPU_LAYERS_AUTO_VALUE if self.gpu_layers_auto else str(self.gpu_layers)]
        # b10453 等新版本 --flash-attn 需要取值 auto|on|off
        cmd += ["--flash-attn", "on" if self.flash_attention else "off"]
        cmd += ["--cache-type-k", self.cache_type_k]
        cmd += ["--cache-type-v", self.cache_type_v]
        if self.cpu_moe:
            cmd += ["--cpu-moe"]
        elif self.n_cpu_moe > 0:
            cmd += ["--n-cpu-moe", str(self.n_cpu_moe)]
        if self.spec_mtp:
            cmd += ["--spec-type", "draft-mtp"]
            cmd += ["--spec-draft-n-max", str(self.spec_draft_n_max)]
        cmd += ["--host", self.host]
        cmd += ["--port", str(self.port)]
        if self.alias.strip():
            cmd += ["--alias", self.alias.strip()]
        cmd += self._advanced_args()
        cmd += split_extra_args(self.extra_args)
        return cmd

    def _advanced_args(self) -> list[str]:
        """高级参数：仅在与默认值不同时生成，保持命令干净。"""
        args: list[str] = []
        if self.ubatch_size > 0:
            args += ["-ub", str(self.ubatch_size)]
        if self.threads_batch > 0:
            args += ["-tb", str(self.threads_batch)]
        if self.parallel > 0:
            args += ["-np", str(self.parallel)]
        if not self.cont_batching:
            args += ["--no-cont-batching"]
        if self.no_mmap:
            args += ["--no-mmap"]
        if self.mlock:
            args += ["--mlock"]
        if self.no_warmup:
            args += ["--no-warmup"]
        if self.cache_reuse > 0:
            args += ["--cache-reuse", str(self.cache_reuse)]
        if self.rope_scaling != "none":
            args += ["--rope-scaling", self.rope_scaling]
        if self.rope_freq_scale > 0:
            args += ["--rope-freq-scale", f"{self.rope_freq_scale:g}"]
        if self.temp >= 0:
            args += ["--temp", f"{self.temp:g}"]
        if self.top_k >= 0:
            args += ["--top-k", str(self.top_k)]
        if self.top_p >= 0:
            args += ["--top-p", f"{self.top_p:g}"]
        if self.min_p >= 0:
            args += ["--min-p", f"{self.min_p:g}"]
        return args

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
        if self.mmproj_path.strip():
            mm = Path(self.mmproj_path)
            if not mm.is_file():
                errors.append("mmproj 文件不存在")
        if not self.host.strip():
            errors.append("Host 不能为空")
        if not (1 <= self.port <= 65535):
            errors.append("端口必须在 1-65535 之间")
        if self.cache_type_k not in KV_CACHE_TYPES:
            errors.append(f"非法 KV Cache K 类型: {self.cache_type_k}")
        if self.cache_type_v not in KV_CACHE_TYPES:
            errors.append(f"非法 KV Cache V 类型: {self.cache_type_v}")
        if self.rope_scaling not in ROPE_SCALING_TYPES:
            errors.append(f"非法 RoPE 缩放类型: {self.rope_scaling}")
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
        """预设只保存调参字段，不含 exe / 模型 / mmproj 路径。"""
        d = self.to_dict()
        d.pop("llama_server_path", None)
        d.pop("model_path", None)
        d.pop("mmproj_path", None)
        return d
