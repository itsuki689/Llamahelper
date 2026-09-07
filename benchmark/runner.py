"""自动 Benchmark：按参数区间批量启动 llama-server 并测量性能。

gen 模式：启动进程 → 等待 /health 就绪 → 预热 → POST /completion 生成固定
token → 记录 generation/prompt tok/s、显存/功耗/RAM 峰值 → 关闭进程。
asr 模式：对目录中的每段参考音频发识别请求，RTF = 音频时长 / 识别耗时。
全部在 QThread 中执行，不阻塞 UI。
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path

import psutil
from PySide6.QtCore import QThread, Signal

from benchmark.audio import audio_duration, list_audio_files
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
    mode: str = "gen"                # "gen"（生成 tok/s）| "asr"（语音识别 RTF）
    sweep_field: str = "n_cpu_moe"   # 扫描目标（见 SWEEP_FIELDS）
    sweep_start: int = 48
    sweep_end: int = 16
    sweep_step: int = 8
    gen_tokens: int = 200
    warmup_tokens: int = 32          # gen 模式：正式测量前的预热 token 数
    include_cpu_moe: bool = True     # 仅扫描 ncmoe 时有效：加测 --cpu-moe 基准轮
    prompt: str = DEFAULT_TEST_PROMPT
    audio_dir: str = ""              # asr 模式：参考音频目录（用户放入 wav/flac/mp3）
    health_timeout: float = 180.0


# 可扫描的参数：显示名 → (ServerParams 字段, 额外覆盖)
SWEEP_FIELDS: dict[str, tuple[str, dict]] = {
    "n-cpu-moe":      ("n_cpu_moe", {"cpu_moe": False}),
    "GPU Layers":     ("gpu_layers", {"gpu_layers_auto": False}),
    "Batch Size":     ("batch_size", {}),
    "ubatch Size":    ("ubatch_size", {}),
    "Threads":        ("threads", {}),
    "Threads Batch":  ("threads_batch", {}),
    "Context Size":   ("context_size", {}),
    "MTP draft n-max": ("spec_draft_n_max", {"spec_mtp": True}),
}


def build_config_list(job: BenchJob) -> list[dict]:
    """生成待测配置列表：[{"label":..., "overrides":{...}}, ...]"""
    if job.sweep_field not in {f for f, _ in SWEEP_FIELDS.values()}:
        raise ValueError(f"未知扫描参数: {job.sweep_field}")
    configs: list[dict] = []
    if job.sweep_field == "n_cpu_moe" and job.include_cpu_moe:
        configs.append({"label": "cpu-moe (全部MoE在CPU)",
                        "overrides": {"cpu_moe": True, "n_cpu_moe": 0}})
    field, extra = next((f, e) for f, e in SWEEP_FIELDS.values()
                        if f == job.sweep_field)
    step = max(1, abs(job.sweep_step))
    v = job.sweep_start
    while True:
        configs.append({"label": f"{field}={v}",
                        "overrides": {**extra, field: v}})
        if job.sweep_start >= job.sweep_end:
            v -= step
            if v < job.sweep_end:
                break
        else:
            v += step
            if v > job.sweep_end:
                break
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
        self._stop_event = threading.Event()
        self._proc_lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._request_threads: list[threading.Thread] = []

    def stop(self) -> None:
        self._stop_requested = True
        self._stop_event.set()
        # Closing our benchmark child's connections also unblocks warmup I/O.
        with self._proc_lock:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.kill()

    # ------------------------------------------------------------------
    def run(self) -> None:
        if self.job.mode == "asr" and not list_audio_files(self.job.audio_dir):
            self.sig_log.emit(
                f"[bench] 音频目录中没有 wav/flac/mp3 文件: {self.job.audio_dir}")
            self.sig_finished_all.emit()
            return
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
                self._stop_event.wait(2.0)  # 轮次间隔，可立即中止
        self.sig_finished_all.emit()

    # ------------------------------------------------------------------
    def _run_one(self, label: str, overrides: dict) -> dict:
        params = replace(self.job.base_params, **overrides)
        result = {
            "label": label,
            "field": self.job.sweep_field,
            "value": overrides.get(self.job.sweep_field, ""),
            "cpu_moe": overrides.get("cpu_moe", False),
            "gen_tps": 0.0, "prompt_tps": 0.0,
            # ASR 模式指标：RTF = 音频总时长 / 识别总耗时（越大越快）
            "rtf": 0.0, "audio_s": 0.0, "infer_s": 0.0, "asr_files": 0,
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

        with self._proc_lock:
            self._proc = proc
            if self._stop_requested and proc.poll() is None:
                proc.kill()

        reader = threading.Thread(target=self._read_output,
                                  args=(proc, lines), daemon=True)
        reader.start()

        try:
            ready = self._wait_ready(proc, params, result)
            if ready and not self._stop_requested:
                if self.job.mode == "asr":
                    self._measure_asr(proc, params, result)
                else:
                    self._warmup(params)
                    if not self._stop_requested:
                        self._sample_peaks(proc, result)
                        self._measure(proc, params, result)
            elif self._stop_requested:
                result["status"] = "中止"
                result["error"] = "用户中止"
        finally:
            self._shutdown(proc)
            for request_thread in self._request_threads:
                request_thread.join()
            self._request_threads.clear()
            reader.join()
            proc.stdout.close()
            with self._proc_lock:
                self._proc = None
            if not self._stop_requested:
                self._wait_port_free(params.host, params.port)

        if self._stop_requested:
            result.update(ok=False, status="中止", error="用户中止")
            return result

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

    def _post_completion(self, params: ServerParams, n_predict: int,
                         timeout: float) -> dict:
        """发送一次 /completion 请求，返回响应 JSON。"""
        url = f"http://{params.host}:{params.port}/completion"
        payload = json.dumps({
            "prompt": self.job.prompt,
            "n_predict": n_predict,
            "temperature": 0.0,
            "stream": False,
            "cache_prompt": False,
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _warmup(self, params: ServerParams) -> None:
        """正式测量前的预热请求（结果不计入），失败仅记日志不中断。"""
        if self._stop_requested or self.job.warmup_tokens <= 0:
            return
        self.sig_log.emit(f"[bench] 预热 {self.job.warmup_tokens} tokens …")
        try:
            self._post_completion(params, self.job.warmup_tokens,
                                  timeout=max(60.0, self.job.warmup_tokens * 2.0))
            if not self._stop_requested:
                self.sig_log.emit("[bench] 预热完成，开始正式测量")
        except Exception as e:
            if not self._stop_requested:
                self.sig_log.emit(f"[bench] 预热请求失败（继续测量）: {e}")

    def _measure(self, proc: subprocess.Popen, params: ServerParams,
                 result: dict) -> None:
        holder: dict = {}

        def do_request() -> None:
            try:
                holder["data"] = self._post_completion(
                    params, self.job.gen_tokens,
                    timeout=max(120.0, self.job.gen_tokens * 2.0))
            except Exception as e:  # 网络/JSON 错误统一记录
                holder["error"] = str(e)

        t = threading.Thread(target=do_request, daemon=True)
        self._request_threads.append(t)
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

    # ------------------------------------------------------------------
    # ASR 模式：RTF = 音频时长 / 识别耗时
    def _post_audio(self, params: ServerParams, audio_path,
                    timeout: float = 300.0) -> None:
        """发送一次音频识别请求（OpenAI 兼容 input_audio），返回 None。"""
        audio_path = Path(audio_path)
        b64 = base64.b64encode(audio_path.read_bytes()).decode("ascii")
        fmt = audio_path.suffix.lstrip(".").lower()
        url = f"http://{params.host}:{params.port}/v1/chat/completions"
        payload = json.dumps({
            "messages": [{"role": "user", "content": [
                {"type": "input_audio",
                 "input_audio": {"data": b64, "format": fmt}},
                {"type": "text",
                 "text": "Transcribe the audio into text. "
                         "Output only the transcription."},
            ]}],
            "temperature": 0.0,
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()

    def _measure_asr(self, proc: subprocess.Popen, params: ServerParams,
                     result: dict) -> None:
        files = list_audio_files(self.job.audio_dir)
        if not files:
            result["error"] = f"音频目录中没有 wav/flac/mp3 文件: {self.job.audio_dir}"
            return
        # 预热：用第一个文件跑一次，不计入统计
        self.sig_log.emit(f"[bench] ASR 预热（{files[0].name}，不计入）…")
        try:
            self._post_audio(params, files[0], timeout=120.0)
        except Exception as e:
            if not self._stop_requested:
                self.sig_log.emit(f"[bench] ASR 预热失败（继续测量）: {e}")
        if self._stop_requested:
            return
        self._sample_peaks(proc, result)

        total_audio = 0.0
        total_infer = 0.0
        done = 0
        for f in files:
            if self._stop_requested:
                break
            if proc.poll() is not None:
                result["error"] = f"识别过程中进程退出，exit code = {proc.returncode}"
                return
            try:
                dur = audio_duration(f)
            except ValueError as e:
                self.sig_log.emit(f"[bench] 跳过 {f.name}: {e}")
                continue
            holder: dict = {}

            def do_request(path=f) -> None:
                t0 = time.perf_counter()
                try:
                    self._post_audio(params, path)
                    holder["elapsed"] = time.perf_counter() - t0
                except Exception as e:
                    holder["error"] = str(e)

            t = threading.Thread(target=do_request, daemon=True)
            self._request_threads.append(t)
            t.start()
            while t.is_alive():
                if self._stop_requested:
                    return
                self._sample_peaks(proc, result)
                time.sleep(0.3)
            t.join(timeout=10)
            if "error" in holder:
                self.sig_log.emit(f"[bench] {f.name} 识别失败: {holder['error']}")
                continue
            elapsed = holder.get("elapsed", 0.0)
            if elapsed <= 0:
                continue
            total_audio += dur
            total_infer += elapsed
            done += 1
            self.sig_log.emit(
                f"[bench] {f.name}: 音频 {dur:.2f}s / 识别 {elapsed:.2f}s "
                f"= RTF {dur / elapsed:.2f}")

        if done and total_infer > 0:
            result["rtf"] = total_audio / total_infer
            result["audio_s"] = total_audio
            result["infer_s"] = total_infer
            result["asr_files"] = done
            result["ok"] = True
            result["status"] = "OK"
            self.sig_log.emit(
                f"[bench] {result['label']}: 平均 RTF {result['rtf']:.2f} "
                f"({done} 个文件)")
        elif not result["error"]:
            result["error"] = "没有成功识别的音频文件"

    def _shutdown(self, proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()  # 后台等待回收，不能带着仍运行的子进程报告完成

    @staticmethod
    def _wait_port_free(host: str, port: int, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and port_in_use(host, port):
            time.sleep(0.3)
