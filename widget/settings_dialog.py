"""In-app editor for translation prompt presets."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from widget.api_client import DEFAULT_TRANSLATE_PROMPT
from widget.prompt_presets import (
    active_prompt_name, normalize_prompt_presets, save_prompt_presets,
)
from widget.settings import WidgetSettings


class PromptSettingsDialog(QDialog):
    """Edit named prompts and select the prompt used by the desktop widget."""

    prompt_applied = Signal(str, str)

    def __init__(self, settings: WidgetSettings | None = None, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings or WidgetSettings()
        self.setWindowTitle("翻译挂件设置")
        self.resize(720, 460)
        self.setMinimumSize(620, 400)
        self.setModal(False)
        self._presets: list[dict[str, str]] = []
        self._active_name = ""
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)
        title = QLabel("翻译挂件 · 提示词预设")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        hint = QLabel("保存并应用后，桌面挂件与吸附态预设菜单会立即同步。")
        hint.setStyleSheet("color: #98989D; font-size: 11px;")
        root.addWidget(title)
        root.addWidget(hint)

        content = QHBoxLayout()
        content.setSpacing(14)
        left = QFrame()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.addWidget(QLabel("预设"))
        self.preset_list = QListWidget()
        self.preset_list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.preset_list.currentItemChanged.connect(self._load_selected)
        left_lay.addWidget(self.preset_list, 1)
        left_buttons = QHBoxLayout()
        self.btn_new = QPushButton("新建")
        self.btn_delete = QPushButton("删除")
        self.btn_new.clicked.connect(self._new_preset)
        self.btn_delete.clicked.connect(self._delete_selected)
        left_buttons.addWidget(self.btn_new)
        left_buttons.addWidget(self.btn_delete)
        left_lay.addLayout(left_buttons)
        content.addWidget(left, 1)

        right = QFrame()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.addWidget(QLabel("预设名称"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：中英互译")
        right_lay.addWidget(self.name_edit)
        right_lay.addWidget(QLabel("系统提示词"))
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("输入翻译挂件使用的系统提示词…")
        right_lay.addWidget(self.prompt_edit, 1)
        action_row = QHBoxLayout()
        self.active_label = QLabel()
        self.active_label.setStyleSheet("color: #30D158; font-size: 11px;")
        self.btn_reset = QPushButton("恢复默认提示词")
        self.btn_save = QPushButton("保存并应用")
        self.btn_save.setObjectName("primaryBtn")
        self.btn_reset.clicked.connect(
            lambda: self.prompt_edit.setPlainText(DEFAULT_TRANSLATE_PROMPT))
        self.btn_save.clicked.connect(self._save_current)
        action_row.addWidget(self.active_label)
        action_row.addStretch(1)
        action_row.addWidget(self.btn_reset)
        action_row.addWidget(self.btn_save)
        right_lay.addLayout(action_row)
        content.addWidget(right, 2)
        root.addLayout(content, 1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_button = QPushButton("完成")
        close_button.clicked.connect(self.close)
        close_row.addWidget(close_button)
        root.addLayout(close_row)

    @property
    def presets(self) -> list[dict[str, str]]:
        return [dict(item) for item in self._presets]

    @property
    def active_name(self) -> str:
        return self._active_name

    def reload(self) -> None:
        data = self.settings.load()
        self._presets = normalize_prompt_presets(data)
        self._active_name = active_prompt_name(data, self._presets)
        self._rebuild_list(self._active_name)

    def _rebuild_list(self, selected_name: str = "") -> None:
        self.preset_list.blockSignals(True)
        self.preset_list.clear()
        selected_item = None
        for preset in self._presets:
            label = preset["name"]
            if label == self._active_name:
                label += "  · 当前"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, preset["name"])
            self.preset_list.addItem(item)
            if preset["name"] == selected_name:
                selected_item = item
        self.preset_list.blockSignals(False)
        if selected_item is None and self.preset_list.count():
            selected_item = self.preset_list.item(0)
        if selected_item is not None:
            self.preset_list.setCurrentItem(selected_item)
            self._load_selected(selected_item)

    def _load_selected(self, current, previous=None) -> None:
        del previous
        if current is None:
            return
        name = str(current.data(Qt.ItemDataRole.UserRole))
        preset = next((item for item in self._presets
                       if item["name"] == name), None)
        if preset is None:
            return
        self.name_edit.setText(preset["name"])
        self.prompt_edit.setPlainText(preset["prompt"])
        self.active_label.setText(
            "当前使用" if name == self._active_name else "")

    def _new_preset(self) -> None:
        existing = {item["name"] for item in self._presets}
        index = 1
        name = "新预设"
        while name in existing:
            index += 1
            name = f"新预设 {index}"
        self.preset_list.clearSelection()
        self.preset_list.setCurrentItem(None)
        self.name_edit.setText(name)
        self.prompt_edit.setPlainText(DEFAULT_TRANSLATE_PROMPT)
        self.active_label.clear()
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _save_current(self) -> None:
        name = self.name_edit.text().strip()
        prompt = self.prompt_edit.toPlainText().strip()
        if not name or not prompt:
            QMessageBox.warning(self, "无法保存", "预设名称和提示词不能为空。")
            return
        current = self.preset_list.currentItem()
        old_name = (str(current.data(Qt.ItemDataRole.UserRole))
                    if current is not None else "")
        duplicate = next((item for item in self._presets
                          if item["name"] == name and item["name"] != old_name), None)
        if duplicate is not None:
            QMessageBox.warning(self, "无法保存", "已存在同名预设。")
            return
        replaced = False
        for item in self._presets:
            if item["name"] == old_name:
                item.update({"name": name, "prompt": prompt})
                replaced = True
                break
        if not replaced:
            self._presets.append({"name": name, "prompt": prompt})
        self._active_name = name
        save_prompt_presets(self.settings, self._presets, self._active_name)
        self._rebuild_list(name)
        self.prompt_applied.emit(name, prompt)

    def _delete_selected(self) -> None:
        current = self.preset_list.currentItem()
        if current is None:
            return
        if len(self._presets) <= 1:
            QMessageBox.information(self, "保留预设", "至少需要保留一个提示词预设。")
            return
        name = str(current.data(Qt.ItemDataRole.UserRole))
        self._presets = [item for item in self._presets if item["name"] != name]
        if self._active_name == name:
            self._active_name = self._presets[0]["name"]
        save_prompt_presets(self.settings, self._presets, self._active_name)
        self._rebuild_list(self._active_name)
        active = next(item for item in self._presets
                      if item["name"] == self._active_name)
        self.prompt_applied.emit(active["name"], active["prompt"])
