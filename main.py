"""LlamaHelper — llama.cpp GUI 调参与性能测试工具（Windows 11 桌面版）。

用法: python main.py
打包: build.bat (PyInstaller 单文件 exe)
"""
from __future__ import annotations

import sys
from pathlib import Path

# 保证以任意工作目录启动时都能 import 项目包
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.theme import APP_QSS


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("LlamaHelper")
    app.setOrganizationName("LlamaHelper")
    app.setStyleSheet(APP_QSS)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
