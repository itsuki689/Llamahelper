"""从 llama.cpp / llama-server 的 stdout 日志中解析性能数据。

兼容两种输出格式：
  llama_perf_context_print: prompt eval time =  123.45 ms /   50 tokens ( 2.47 ms per token, 405.06 tokens per second)
  llama_perf_context_print:        eval time = 3456.78 ms /  200 runs   (17.28 ms per token,  57.86 tokens per second)
  llama_perf_context_print:        load time = 1523.45 ms
  slot print_timing: ...（llama-server 每个请求完成时打印，结构相同）
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_LOAD_RE = re.compile(r"load time\s*=\s*([\d.]+)\s*ms")
_PROMPT_RE = re.compile(
    r"prompt eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens?"
    r"[^0-9]*\([^)]*?([\d.]+)\s*tokens? per second"
)
_EVAL_RE = re.compile(
    r"(?<!prompt )eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*(?:runs|tokens?)"
    r"[^0-9]*\([^)]*?([\d.]+)\s*tokens? per second"
)


@dataclass
class PerfEvent:
    """一条解析出的性能事件。kind: load / prompt_eval / eval"""
    kind: str
    ms: float = 0.0
    tokens: int = 0
    tps: float = 0.0


def parse_perf_line(line: str) -> PerfEvent | None:
    """解析单行日志，命中则返回 PerfEvent，否则返回 None。"""
    m = _PROMPT_RE.search(line)
    if m:
        return PerfEvent("prompt_eval", ms=float(m.group(1)),
                         tokens=int(m.group(2)), tps=float(m.group(3)))
    m = _EVAL_RE.search(line)
    if m:
        return PerfEvent("eval", ms=float(m.group(1)),
                         tokens=int(m.group(2)), tps=float(m.group(3)))
    m = _LOAD_RE.search(line)
    if m:
        return PerfEvent("load", ms=float(m.group(1)))
    return None
