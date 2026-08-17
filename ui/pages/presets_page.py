"""Presets 页面：预设列表管理（应用/保存/删除/恢复默认）。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QInputDialog, QLabel, QListWidget, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from config.presets import DEFAULT_PRESETS, PresetManager
from ui.theme import GRAY


class PresetsPage(QWidget):
    apply_requested = Signal(str)  # 预设名
    save_requested = Signal(str)   # 新预设名（由 MainWindow 保存当前参数）

    def __init__(self, manager: PresetManager, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        title = QLabel("Presets")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        hint = QLabel("预设只保存调参字段（不含 llama-server / 模型路径），"
                      "持久化在程序目录 presets.json。")
        hint.setStyleSheet(f"color: {GRAY};")
        hint.setWordWrap(True)
        root.addWidget(hint)

        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _: self._apply())
        body.addWidget(self.list, 1)

        side = QVBoxLayout()
        self.btn_apply = QPushButton("应用到参数")
        self.btn_apply.setObjectName("primaryBtn")
        self.btn_save = QPushButton("保存当前参数为预设…")
        self.btn_delete = QPushButton("删除所选")
        self.btn_delete.setObjectName("dangerBtn")
        self.btn_defaults = QPushButton("恢复默认预设")
        self.btn_apply.clicked.connect(self._apply)
        self.btn_save.clicked.connect(self._save_current)
        self.btn_delete.clicked.connect(self._delete)
        self.btn_defaults.clicked.connect(self._restore_defaults)
        for b in (self.btn_apply, self.btn_save, self.btn_delete,
                  self.btn_defaults):
            side.addWidget(b)
        side.addStretch(1)
        body.addLayout(side)
        root.addLayout(body, 1)

        # 预设详情预览
        self.detail = QLabel("")
        self.detail.setStyleSheet(f"color: {GRAY};")
        self.detail.setWordWrap(True)
        self.list.currentTextChanged.connect(self._show_detail)
        root.addWidget(self.detail)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        current = self.list.currentItem()
        name = current.text() if current else ""
        self.list.clear()
        self.list.addItems(self.manager.names())
        items = self.list.findItems(name, Qt.MatchFlag.MatchExactly)
        if items:
            self.list.setCurrentItem(items[0])
        elif self.list.count():
            self.list.setCurrentRow(0)

    def _selected(self) -> str | None:
        item = self.list.currentItem()
        return item.text() if item else None

    def _apply(self) -> None:
        name = self._selected()
        if name:
            self.apply_requested.emit(name)

    def _save_current(self) -> None:
        name, ok = QInputDialog.getText(self, "保存预设", "预设名称：")
        name = name.strip()
        if ok and name:
            self.save_requested.emit(name)

    def _delete(self) -> None:
        name = self._selected()
        if not name:
            return
        if QMessageBox.question(self, "删除预设",
                                f"确定删除预设「{name}」？") == QMessageBox.StandardButton.Yes:
            self.manager.delete(name)
            self.refresh()

    def _restore_defaults(self) -> None:
        if QMessageBox.question(
                self, "恢复默认", "将默认预设（Qwen35B 系列等）合并回列表？"
        ) == QMessageBox.StandardButton.Yes:
            for k, v in DEFAULT_PRESETS.items():
                self.manager._presets.setdefault(k, dict(v))
            self.manager.save()
            self.refresh()

    def _show_detail(self, name: str) -> None:
        data = self.manager.get(name)
        if not data:
            self.detail.setText("")
            return
        parts = [f"{k}={v}" for k, v in data.items()]
        self.detail.setText(" · ".join(parts))

