"""Logs 页面：实时日志显示、级别高亮、自动滚动、保存。"""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QVBoxLayout, QWidget,
)

from ui.theme import MONO_STACK, log_line_color


class LogsPage(QWidget):
    MAX_BLOCKS = 8000  # 防止长时间运行内存膨胀

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        title = QLabel("Logs")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        bar = QFrame()
        bar.setObjectName("card")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 8, 12, 8)
        self.chk_pause = QCheckBox("暂停自动滚动")
        self.btn_save = QPushButton("保存日志…")
        self.btn_clear = QPushButton("清空")
        self.btn_save.clicked.connect(self._save)
        self.btn_clear.clicked.connect(self._clear)
        self.line_count = QLabel("0 行")
        self.line_count.setStyleSheet("color: #98989D;")
        bl.addWidget(self.chk_pause)
        bl.addStretch(1)
        bl.addWidget(self.line_count)
        bl.addWidget(self.btn_clear)
        bl.addWidget(self.btn_save)
        root.addWidget(bar)

        self.view = QTextEdit()
        self.view.setReadOnly(True)
        self.view.setStyleSheet(f"font-family: {MONO_STACK}; font-size: 12px;")
        self.view.document().setMaximumBlockCount(self.MAX_BLOCKS)
        root.addWidget(self.view, 1)

    # ------------------------------------------------------------------
    def append_line(self, line: str) -> None:
        color = log_line_color(line)
        self.view.append(
            f'<span style="color:{color};">{html.escape(line)}</span>')
        self.line_count.setText(
            f"{self.view.document().blockCount()} 行")
        if not self.chk_pause.isChecked():
            sb = self.view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _save(self) -> None:
        default = f"llama_server_{datetime.now():%Y%m%d_%H%M%S}.log"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存日志", default, "日志文件 (*.log *.txt)")
        if path:
            Path(path).write_text(self.view.toPlainText(), encoding="utf-8")

    def _clear(self) -> None:
        self.view.clear()
        self.line_count.setText("0 行")
