"""应用配置持久化：路径/参数保存到程序目录下的 JSON 文件。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from core.params import ServerParams

CONFIG_FILE_NAME = "llama_helper_config.json"


def app_dir() -> Path:
    """程序目录：PyInstaller 打包后为 exe 所在目录，否则为项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


class AppConfig:
    """读写 llama_helper_config.json，自动记住上次路径与参数。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (app_dir() / CONFIG_FILE_NAME)

    def load_params(self) -> ServerParams:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return ServerParams()
        if not isinstance(data, dict):
            return ServerParams()
        return ServerParams.from_dict(data.get("params", {}))

    def save_params(self, params: ServerParams) -> None:
        payload = {"params": params.to_dict()}
        try:
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass  # 配置保存失败不应影响主流程
