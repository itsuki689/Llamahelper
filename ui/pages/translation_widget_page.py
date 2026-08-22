"""翻译挂件全页设置：启动入口与提示词预设管理。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
    QWidget,
)

from widget.api_client import DEFAULT_TRANSLATE_PROMPT
from widget.prompt_presets import (
    active_prompt_name, normalize_prompt_presets, save_prompt_presets,
)
from widget.settings import WidgetSettings


class TranslationWidgetPage(QWidget):
    """在主窗口内管理并启动桌面翻译挂件。"""

    launch_requested = Signal()
    prompt_applied = Signal(str, str)

    def __init__(self, settings: WidgetSettings | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.settings = settings or WidgetSettings()
        self._presets: list[dict[str, str]] = []
        self._active_name = ""
        self._widget_running = False
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        title = QLabel("翻译挂件")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        launch_card = QFrame()
        launch_card.setObjectName("card")
        launch_layout = QHBoxLayout(launch_card)
        launch_layout.setContentsMargins(18, 16, 18, 16)
        launch_layout.setSpacing(14)
        launch_copy = QVBoxLayout()
        launch_copy.setSpacing(4)
        launch_title = QLabel("桌面翻译挂件")
        launch_title.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.status_label = QLabel()
        self.status_label.setObjectName("statSub")
        launch_copy.addWidget(launch_title)
        launch_copy.addWidget(self.status_label)
        launch_layout.addLayout(launch_copy, 1)
        self.btn_launch = QPushButton()
        self.btn_launch.setObjectName("primaryBtn")
        self.btn_launch.setMinimumWidth(118)
        self.btn_launch.clicked.connect(self.launch_requested)
        launch_layout.addWidget(self.btn_launch)
        root.addWidget(launch_card)

        editor_card = QFrame()
        editor_card.setObjectName("card")
        editor_layout = QVBoxLayout(editor_card)
        editor_layout.setContentsMargins(18, 16, 18, 16)
        editor_layout.setSpacing(12)
        section_title = QLabel("提示词预设")
        section_title.setStyleSheet("font-size: 15px; font-weight: 700;")
        section_hint = QLabel(
            "保存并应用后，桌面挂件与吸附态右键预设菜单会立即同步。")
        section_hint.setObjectName("statSub")
        editor_layout.addWidget(section_title)
        editor_layout.addWidget(section_hint)

        content = QHBoxLayout()
        content.setSpacing(16)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        left_layout.addWidget(QLabel("预设"))
        self.preset_list = QListWidget()
        self.preset_list.setMinimumWidth(210)
        self.preset_list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.preset_list.currentItemChanged.connect(self._load_selected)
        left_layout.addWidget(self.preset_list, 1)
        preset_actions = QHBoxLayout()
        self.btn_new = QPushButton("新建")
        self.btn_delete = QPushButton("删除")
        self.btn_new.clicked.connect(self._new_preset)
        self.btn_delete.clicked.connect(self._delete_selected)
        preset_actions.addWidget(self.btn_new)
        preset_actions.addWidget(self.btn_delete)
        left_layout.addLayout(preset_actions)
        content.addWidget(left, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        right_layout.addWidget(QLabel("预设名称"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：中英互译")
        right_layout.addWidget(self.name_edit)
        right_layout.addWidget(QLabel("系统提示词"))
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("输入翻译挂件使用的系统提示词…")
        right_layout.addWidget(self.prompt_edit, 1)
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
        right_layout.addLayout(action_row)
        content.addWidget(right, 2)
        editor_layout.addLayout(content, 1)
        root.addWidget(editor_card, 1)

        self.set_widget_running(False)

    @property
    def presets(self) -> list[dict[str, str]]:
        return [dict(item) for item in self._presets]

    @property
    def active_name(self) -> str:
        return self._active_name

    def set_widget_running(self, running: bool) -> None:
        self._widget_running = running
        if running:
            self.status_label.setText("挂件正在运行 · 可点击按钮将它唤到前台")
            self.btn_launch.setText("唤起挂件")
        else:
            self.status_label.setText("挂件未启动 · 启动后可拖动并吸附到屏幕边缘")
            self.btn_launch.setText("启动挂件")

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
                          if item["name"] == name
                          and item["name"] != old_name), None)
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
            QMessageBox.information(
                self, "保留预设", "至少需要保留一个提示词预设。")
            return
        name = str(current.data(Qt.ItemDataRole.UserRole))
        self._presets = [
            item for item in self._presets if item["name"] != name]
        if self._active_name == name:
            self._active_name = self._presets[0]["name"]
        save_prompt_presets(self.settings, self._presets, self._active_name)
        self._rebuild_list(self._active_name)
        active = next(item for item in self._presets
                      if item["name"] == self._active_name)
        self.prompt_applied.emit(active["name"], active["prompt"])
