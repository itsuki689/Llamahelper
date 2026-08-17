"""配置持久化与预设管理测试。"""
from config.app_config import AppConfig
from config.presets import DEFAULT_PRESETS, PresetManager
from core.params import ServerParams


def test_app_config_roundtrip(tmp_path):
    cfg = AppConfig(tmp_path / "config.json")
    p = ServerParams(llama_server_path="C:/x/llama-server.exe",
                     model_path="D:/m.gguf", n_cpu_moe=24)
    cfg.save_params(p)
    loaded = cfg.load_params()
    assert loaded.llama_server_path == p.llama_server_path
    assert loaded.model_path == p.model_path
    assert loaded.n_cpu_moe == 24


def test_app_config_missing_file(tmp_path):
    cfg = AppConfig(tmp_path / "nope.json")
    assert cfg.load_params() == ServerParams()


def test_presets_default_created(tmp_path):
    pm = PresetManager(tmp_path / "presets.json")
    for name in DEFAULT_PRESETS:
        assert name in pm.names()
    assert (tmp_path / "presets.json").exists()


def test_presets_put_delete(tmp_path):
    pm = PresetManager(tmp_path / "presets.json")
    p = ServerParams(llama_server_path="C:/x.exe", model_path="D:/m.gguf",
                     context_size=4096)
    pm.put("MyPreset", p)
    data = pm.get("MyPreset")
    assert data and data["context_size"] == 4096
    assert "llama_server_path" not in data
    pm.delete("MyPreset")
    assert pm.get("MyPreset") is None


def test_presets_apply_keeps_paths(tmp_path):
    pm = PresetManager(tmp_path / "presets.json")
    pm.put("P1", ServerParams(context_size=32768, n_cpu_moe=48))
    current = ServerParams(llama_server_path="C:/x.exe",
                           model_path="D:/m.gguf", context_size=8192)
    merged = pm.apply_to("P1", current)
    assert merged.context_size == 32768
    assert merged.n_cpu_moe == 48
    assert merged.model_path == "D:/m.gguf"
