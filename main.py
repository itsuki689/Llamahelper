"""LlamaHelper — llama.cpp GUI 调参与性能测试工具（Windows 11 桌面版）。

用法: python main.py
打包: build.bat (PyInstaller 单文件 exe)
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

# 保证以任意工作目录启动时都能 import 项目包
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.theme import APP_QSS


def resource_path(rel: str) -> Path:
    """打包后资源在 sys._MEIPASS，开发时在项目根目录。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / rel


def set_windows_app_id() -> None:
    """让 Windows 标题栏、任务栏和 EXE 使用同一个应用身份。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "itsuki689.LlamaHelper")
    except (AttributeError, OSError):
        pass


def load_app_icon() -> QIcon:
    """优先加载含多尺寸条目的 ICO，PNG 作为开发环境回退。"""
    for rel in ("assets/icon.ico", "assets/icon.png"):
        path = resource_path(rel)
        if path.is_file():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return QIcon()


def main() -> int:
    set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("LlamaHelper")
    app.setOrganizationName("LlamaHelper")
    app.setStyleSheet(APP_QSS)
    icon = load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    win = MainWindow()
    if not icon.isNull():
        win.setWindowIcon(icon)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
