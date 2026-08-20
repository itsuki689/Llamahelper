"""挂件自身的小配置：自定义提示词、窗口位置，存程序目录 widget_config.json。"""
from __future__ import annotations

import json
from pathlib import Path

from config.app_config import app_dir


class WidgetSettings:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (app_dir() / "widget_config.json")

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, data: dict) -> None:
        try:
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        except OSError:
            pass
