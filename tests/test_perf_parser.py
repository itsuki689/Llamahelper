"""性能日志解析单元测试。"""
from core.perf_parser import parse_perf_line


def test_parse_load_time():
    ev = parse_perf_line(
        "llama_perf_context_print:        load time =    1523.45 ms")
    assert ev and ev.kind == "load" and abs(ev.ms - 1523.45) < 0.01


def test_parse_prompt_eval():
    ev = parse_perf_line(
        "llama_perf_context_print: prompt eval time =     123.45 ms / "
        "   50 tokens (    2.47 ms per token,   405.06 tokens per second)")
    assert ev and ev.kind == "prompt_eval"
    assert ev.tokens == 50 and abs(ev.tps - 405.06) < 0.01


def test_parse_eval():
    ev = parse_perf_line(
        "llama_perf_context_print:        eval time =    3456.78 ms / "
        "  200 runs   (   17.28 ms per token,    57.86 tokens per second)")
    assert ev and ev.kind == "eval"
    assert ev.tokens == 200 and abs(ev.tps - 57.86) < 0.01


def test_parse_slot_print_timing():
    ev = parse_perf_line(
        "slot print_timing: id   0 | task 1 |        eval time =   "
        "128.00 ms /     32 tokens (    4.00 ms per token,   250.00 tokens per second)")
    assert ev and ev.kind == "eval" and abs(ev.tps - 250.0) < 0.01


def test_parse_unrelated_lines():
    assert parse_perf_line("srv    server_listen: listening on 127.0.0.1:8080") is None
    assert parse_perf_line("") is None
    assert parse_perf_line("hello world") is None
