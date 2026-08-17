"""自动 Benchmark：按 ncmoe 区间批量启动 llama-server 并测量性能。

每轮流程：启动进程 → 等待 /health 就绪 → POST /completion 生成固定 token →
记录 generation/prompt tok/s、显存/功耗/RAM 峰值 → 关闭进程 → 下一轮。
全部在 QThread 中执行，不阻塞 UI。
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace

import psutil
from PySide6.QtCore import QThread, Signal

from core.params import ServerParams
from core.server_manager import port_in_use
from monitoring.gpu_monitor import query_gpu

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_OOM_HINTS = ("out of memory", "cudamalloc", "insufficient memory",
              "ggml_backend_cuda_buffer_type_alloc_buffer",
              "failed to allocate", "cuda_error_out_of_memory")

DEFAULT_TEST_PROMPT = (
    "You are a helpful assistant. Please write a detailed, long-form technical "
    "article explaining how a mixture-of-experts transformer model works, "
    "including routing, expert parallelism, quantization, and inference "
    "optimization on consumer GPUs. Be thorough and structured."
)


@dataclass
class BenchJob:
    base_params: ServerParams
    ncmoe_start: int = 48
    ncmoe_end: int = 16
    ncmoe_step: int = 8
    gen_tokens: int = 200
    include_cpu_moe: bool = True
    prompt: str = DEFAULT_TEST_PROMPT
    health_timeout: float = 180.0


def build_config_list(job: BenchJob) -> list[dict]:
    """生成待测配置列表：[{"label":..., "overrides":{...}}, ...]"""
    configs: list[dict] = []
    if job.include_cpu_moe:
        configs.append({"label": "cpu-moe (全部MoE在CPU)",
                        "overrides": {"cpu_moe": True, "n_cpu_moe": 0}})
    step = max(1, abs(job.ncmoe_step))
    v = job.ncmoe_start
    if job.ncmoe_start >= job.ncmoe_end:
        while v >= job.ncmoe_end:
            configs.append({"label": f"ncmoe {v}",
                            "overrides": {"cpu_moe": False, "n_cpu_moe": v}})
            v -= step
    else:
        while v <= job.ncmoe_end:
            configs.append({"label": f"ncmoe {v}",
                            "overrides": {"cpu_moe": False, "n_cpu_moe": v}})
            v += step
    return configs


class BenchmarkRunner(QThread):
    """顺序执行所有配置的基准测试。"""

    sig_log = Signal(str)
    sig_run_started = Signal(dict)     # {"index", "total", "label"}
    sig_run_result = Signal(dict)      # 单轮结果
    sig_finished_all = Signal()

    def __init__(self, job: BenchJob, parent=None) -> None:
        super().__init__(parent)
        self.job = job
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    # ------------------------------------------------------------------
    def run(self) -> None:
        configs = build_config_list(self.job)
        total = len(configs)
        for i, cfg in enumerate(configs):
            if self._stop_requested:
                self.sig_log.emit("[bench] 用户中止测试")
                break
            self.sig_run_started.emit({"index": i, "total": total,
                                       "label": cfg["label"]})
            result = self._run_one(cfg["label"], cfg["overrides"])
            self.sig_run_result.emit(result)
            if not self._stop_requested and i < total - 1:
                time.sleep(2.0)  # 轮次间隔，让显存/驱动平复
        self.sig_finished_all.emit()

    # ------------------------------------------------------------------
    def _run_one(self, label: str, overrides: dict) -> dict:
        params = replace(self.job.base_params, **overrides)
        result = {
            "label": label, "ncmoe": overrides.get("n_cpu_moe", 0),
            "cpu_moe": overrides.get("cpu_moe", False),
            "gen_tps": 0.0, "prompt_tps": 0.0,
            "vram_peak_mb": 0.0, "power_peak_w": 0.0, "ram_peak_mb": 0.0,
            "ok": False, "status": "失败", "error": "",
        }
        if port_in_use(params.host, params.port):
            result["error"] = f"端口 {params.port} 被占用"
            result["status"] = "失败"
            return result

        argv = params.build_command()
        self.sig_log.emit(f"[bench] 启动 {label}: {' '.join(argv)[:160]}...")
        lines: list[str] = []
        try:
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, creationflags=_NO_WINDOW)
        except OSError as e:
            result["error"] = f"进程启动失败: {e}"
            return result

        reader = threading.Thread(target=self._read_output,
                                  args=(proc, lines), daemon=True)
        reader.start()

        try:
            ready = self._wait_ready(proc, params, result)
            if ready and not self._stop_requested:
                self._measure(proc, params, result)
            elif self._stop_requested:
                result["status"] = "中止"
                result["error"] = "用户中止"
        finally:
            self._shutdown(proc)
            self._wait_port_free(params.host, params.port)

        if self._is_oom(lines):
            result["ok"] = False
            result["status"] = "OOM"
            if not result["error"]:
                result["error"] = "日志中出现显存不足 (out of memory)"
        elif result["status"] == "失败" and not result["error"]:
            tail = "\n".join(lines[-5:])
            result["error"] = f"启动失败或提前退出。日志尾部:\n{tail}"
        return result

    # ------------------------------------------------------------------
    def _read_output(self, proc: subprocess.Popen, lines: list[str]) -> None:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                lines.append(line.rstrip())
        except (ValueError, OSError):
            pass

    @staticmethod
    def _is_oom(lines: list[str]) -> bool:
        blob = "\n".join(lines).lower()
        return any(h in blob for h in _OOM_HINTS)

    def _sample_peaks(self, proc: subprocess.Popen, result: dict) -> None:
        gpu = query_gpu()
        if gpu:
            result["vram_peak_mb"] = max(result["vram_peak_mb"], gpu["mem_used_mb"])
            result["power_peak_w"] = max(result["power_peak_w"], gpu["power_w"])
        try:
            rss = psutil.Process(proc.pid).memory_info().rss / (1024 ** 2)
            result["ram_peak_mb"] = max(result["ram_peak_mb"], rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    def _wait_ready(self, proc: subprocess.Popen, params: ServerParams,
                    result: dict) -> bool:
        url = f"http://{params.host}:{params.port}/health"
        deadline = time.monotonic() + self.job.health_timeout
        while time.monotonic() < deadline:
            if self._stop_requested:
                return False
            if proc.poll() is not None:
                result["error"] = f"进程提前退出，exit code = {proc.returncode}"
                return False
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    if resp.status == 200:
                        self.sig_log.emit("[bench] API 就绪")
                        return True
            except (urllib.error.URLError, OSError):
                pass
            self._sample_peaks(proc, result)
            time.sleep(0.5)
        result["error"] = f"等待 API 就绪超时 ({self.job.health_timeout:.0f}s)"
        return False

    def _measure(self, proc: subprocess.Popen, params: ServerParams,
                 result: dict) -> None:
        url = f"http://{params.host}:{params.port}/completion"
        payload = json.dumps({
            "prompt": self.job.prompt,
            "n_predict": self.job.gen_tokens,
            "temperature": 0.0,
            "stream": False,
            "cache_prompt": False,
        }).encode("utf-8")

        holder: dict = {}

        def do_request() -> None:
            req = urllib.request.Request(url, data=payload,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=max(
                        120.0, self.job.gen_tokens * 2.0)) as resp:
                    holder["data"] = json.loads(resp.read().decode("utf-8"))
            except Exception as e:  # 网络/JSON 错误统一记录
                holder["error"] = str(e)

        t = threading.Thread(target=do_request, daemon=True)
        t.start()
        while t.is_alive():
            if self._stop_requested:
                return
            if proc.poll() is not None:
                result["error"] = f"生成过程中进程退出，exit code = {proc.returncode}"
                t.join(timeout=5)
                return
            self._sample_peaks(proc, result)
            time.sleep(0.5)
        t.join(timeout=5)

        if "error" in holder:
            result["error"] = f"completion 请求失败: {holder['error']}"
            return
        timings = (holder.get("data") or {}).get("timings") or {}
        result["gen_tps"] = float(timings.get("predicted_per_second", 0.0))
        result["prompt_tps"] = float(timings.get("prompt_per_second", 0.0))
        if result["gen_tps"] > 0:
            result["ok"] = True
            result["status"] = "OK"
            self.sig_log.emit(
                f"[bench] {result['label']}: gen {result['gen_tps']:.2f} tok/s, "
                f"prompt {result['prompt_tps']:.2f} tok/s")
        else:
            result["error"] = "响应中缺少 timings 数据"

    def _shutdown(self, proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    @staticmethod
    def _wait_port_free(host: str, port: int, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and port_in_use(host, port):
            time.sleep(0.3)
