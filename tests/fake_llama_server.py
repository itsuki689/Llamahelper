#!/usr/bin/env python3
"""假 llama-server：用于测试进程管理、日志解析、API 健康检查与 Benchmark 流程。

- 接受并忽略 llama-server 的全部命令行参数
- 打印仿真的 llama.cpp 日志（含 load time / perf 行）
- 提供 GET /health 与 POST /completion（返回 timings 字段）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROMPT_TPS = 400.0
GEN_TPS = 250.0


class Handler(BaseHTTPRequestHandler):
    stall_post_marker = ""
    def log_message(self, *args):  # 静默 HTTP 访问日志
        pass

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/health"):
            self._json(200, {"status": "ok"})
        else:
            self._json(200, {"status": "ok"})

    def do_POST(self) -> None:
        if self.stall_post_marker:
            Path(self.stall_post_marker).write_text(self.path, encoding="utf-8")
            time.sleep(60)  # Cancellation tests stop the process to unblock.
            return
        if self.path.startswith("/v1/chat/completions"):
            length = int(self.headers.get("Content-Length", "0"))
            try:
                req = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                req = {}
            msgs = req.get("messages", [])
            user = msgs[-1].get("content", "") if msgs else ""
            if isinstance(user, list):  # 多模态分段
                has_img = any(p.get("type") == "image_url" for p in user)
                user = " ".join(str(p.get("text", "")) for p in user)
                if has_img:
                    user = f"[image] {user}"
            self._json(200, {
                "choices": [{"message": {"role": "assistant",
                                         "content": f"[译] {user}"},
                             "finish_reason": "stop"}],
            })
            return
        if not self.path.startswith("/completion"):
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            req = {}
        n_predict = int(req.get("n_predict", 32))
        prompt = str(req.get("prompt", ""))
        n_prompt = max(1, len(prompt) // 4)

        prompt_ms = n_prompt / PROMPT_TPS * 1000.0
        gen_ms = n_predict / GEN_TPS * 1000.0
        time.sleep((prompt_ms + gen_ms) / 1000.0)

        # 仿真 llama-server 的 slot print_timing 日志
        print(f"slot print_timing: id   0 | task 1 | prompt eval time = "
              f"{prompt_ms:9.2f} ms / {n_prompt:6d} tokens "
              f"({prompt_ms/n_prompt:8.2f} ms per token, {PROMPT_TPS:8.2f} tokens per second)",
              flush=True)
        print(f"slot print_timing: id   0 | task 1 |        eval time = "
              f"{gen_ms:9.2f} ms / {n_predict:6d} tokens "
              f"({gen_ms/n_predict:8.2f} ms per token, {GEN_TPS:8.2f} tokens per second)",
              flush=True)

        self._json(200, {
            "content": "fake response " * n_predict,
            "stop": True,
            "timings": {
                "prompt_n": n_prompt,
                "prompt_ms": prompt_ms,
                "prompt_per_second": PROMPT_TPS,
                "predicted_n": n_predict,
                "predicted_ms": gen_ms,
                "predicted_per_second": GEN_TPS,
            },
        })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--stall-post", default="")
    args, _unknown = parser.parse_known_args()
    Handler.stall_post_marker = args.stall_post

    print("llama_model_loader: loaded meta data with 42 key-value pairs "
          "and 363 tensors from fake-model.gguf", flush=True)
    print("llama_perf_context_print:        load time =    1523.45 ms", flush=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"srv    server_listen: listening on {args.host}:{args.port}", flush=True)
    print("srv    update_slots: all slots are idle", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
