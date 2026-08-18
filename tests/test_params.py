"""命令构建与参数校验的单元测试。"""
from pathlib import Path

from core.params import ServerParams, split_extra_args

# Path 在 Windows 上会规范化为反斜杠，断言统一经 Path 转换以跨平台
MODEL_PATH = str(Path("D:/models/qwen moe/Qwen3-30B.gguf"))


def make_params(**kw) -> ServerParams:
    base = dict(llama_server_path="C:/llama/llama-server.exe",
                model_path=MODEL_PATH)
    base.update(kw)
    return ServerParams(**base)


def test_validate(tmp_path):
    exe = tmp_path / "llama-server.exe"
    exe.write_text("x")
    model = tmp_path / "model.gguf"
    model.write_text("x")
    ok = make_params(llama_server_path=str(exe), model_path=str(model))
    assert ok.validate() == []
    assert make_params(llama_server_path="").validate()  # 缺 exe
    assert make_params(model_path="").validate()         # 缺模型
    assert make_params(port=70000).validate()            # 端口越界


def test_build_command_basic():
    cmd = make_params().build_command()
    assert cmd[0].endswith("llama-server.exe")
    assert cmd[1:3] == ["-m", MODEL_PATH]
    assert "-c" in cmd and "8192" in cmd
    assert "-t" in cmd and "16" in cmd
    assert "-b" in cmd and "1024" in cmd
    # GPU Layers Auto → llama.cpp 原生 auto
    i = cmd.index("-ngl")
    assert cmd[i + 1] == "auto"
    assert cmd[cmd.index("--flash-attn") + 1] == "on"
    assert "--cache-type-k" in cmd and "--cache-type-v" in cmd
    assert "--host" in cmd and "--port" in cmd
    # 默认不启用 cpu-moe / ncmoe
    assert "--cpu-moe" not in cmd
    assert "--n-cpu-moe" not in cmd


def test_build_command_gpu_layers_manual():
    cmd = make_params(gpu_layers_auto=False, gpu_layers=20).build_command()
    assert cmd[cmd.index("-ngl") + 1] == "20"


def test_build_command_moe():
    cmd = make_params(cpu_moe=True).build_command()
    assert "--cpu-moe" in cmd
    assert "--n-cpu-moe" not in cmd
    cmd = make_params(n_cpu_moe=48).build_command()
    assert "--cpu-moe" not in cmd
    assert cmd[cmd.index("--n-cpu-moe") + 1] == "48"


def test_build_command_fa_off_and_alias():
    cmd = make_params(flash_attention=False, alias="qwen35b").build_command()
    assert cmd[cmd.index("--flash-attn") + 1] == "off"
    assert cmd[cmd.index("--alias") + 1] == "qwen35b"


def test_build_command_mtp():
    cmd = make_params(spec_mtp=True, spec_draft_n_max=5).build_command()
    assert cmd[cmd.index("--spec-type") + 1] == "draft-mtp"
    assert cmd[cmd.index("--spec-draft-n-max") + 1] == "5"
    # 默认不启用
    assert "--spec-type" not in make_params().build_command()


def test_build_command_advanced_default_off():
    cmd = make_params().build_command()
    for flag in ("-ub", "-tb", "-np", "--no-cont-batching", "--no-mmap",
                 "--mlock", "--no-warmup", "--cache-reuse", "--rope-scaling",
                 "--rope-freq-scale", "--temp", "--top-k", "--top-p", "--min-p"):
        assert flag not in cmd


def test_build_command_advanced_on():
    p = make_params(ubatch_size=512, parallel=2, cont_batching=False,
                    no_mmap=True, mlock=True, no_warmup=True,
                    cache_reuse=256, rope_scaling="yarn",
                    rope_freq_scale=0.5, temp=0.6, top_k=20, top_p=0.9,
                    min_p=0.05, threads_batch=8)
    cmd = p.build_command()
    assert cmd[cmd.index("-ub") + 1] == "512"
    assert cmd[cmd.index("-tb") + 1] == "8"
    assert cmd[cmd.index("-np") + 1] == "2"
    assert "--no-cont-batching" in cmd
    assert "--no-mmap" in cmd and "--mlock" in cmd
    assert "--no-warmup" in cmd
    assert cmd[cmd.index("--cache-reuse") + 1] == "256"
    assert cmd[cmd.index("--rope-scaling") + 1] == "yarn"
    assert cmd[cmd.index("--rope-freq-scale") + 1] == "0.5"
    assert cmd[cmd.index("--temp") + 1] == "0.6"
    assert cmd[cmd.index("--top-k") + 1] == "20"
    assert cmd[cmd.index("--top-p") + 1] == "0.9"
    assert cmd[cmd.index("--min-p") + 1] == "0.05"


def test_extra_args_split():
    cmd = make_params(extra_args="--ubatch-size 512 --no-warmup").build_command()
    assert cmd[-3:] == ["--ubatch-size", "512", "--no-warmup"]
    assert split_extra_args("") == []


def test_command_display_quotes_paths():
    disp = make_params().command_display()
    assert f'"{MODEL_PATH}"' in disp


def test_roundtrip_and_preset_dict():
    p = make_params(n_cpu_moe=32, cache_type_k="q8_0")
    p2 = ServerParams.from_dict(p.to_dict())
    assert p2 == p
    d = p.preset_dict()
    assert "llama_server_path" not in d and "model_path" not in d
    assert d["n_cpu_moe"] == 32
