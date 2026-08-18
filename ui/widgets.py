"""通用自定义控件。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QSpinBox, QVBoxLayout, QWidget,
)


class StatCard(QFrame):
    """Dashboard 指标卡片：标题 + 大数值 + 副标题。"""

    def __init__(self, title: str, value: str = "--", sub: str = "",
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)
        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("cardTitle")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("statValue")
        self.sub_label = QLabel(sub)
        self.sub_label.setObjectName("statSub")
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.sub_label)

    def set_value(self, value: str, sub: str | None = None) -> None:
        self.value_label.setText(value)
        if sub is not None:
            self.sub_label.setText(sub)

    def set_color(self, color: str) -> None:
        self.value_label.setStyleSheet(f"color: {color};")


class DragDropLineEdit(QLineEdit):
    """支持拖放文件路径的输入框（用于 GGUF 模型路径）。"""

    file_dropped = Signal(str)

    def __init__(self, suffix_filter: str = ".gguf", parent=None) -> None:
        super().__init__(parent)
        self._suffix = suffix_filter.lower()
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(self._suffix):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local.lower().endswith(self._suffix):
                self.setText(str(Path(local)))
                self.file_dropped.emit(str(Path(local)))
                event.acceptProposedAction()
                return
        event.ignore()


class StatusDot(QLabel):
    """服务状态小圆点。"""

    _COLORS = {"stopped": "#636366", "starting": "#FFD60A",
               "running": "#30D158", "stopping": "#FF9F0A"}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self.set_state("stopped")

    def set_state(self, state: str) -> None:
        color = self._COLORS.get(state, "#636366")
        self.setStyleSheet(
            f"background: {color}; border-radius: 6px;")


class FocusWheelGuard(QObject):
    """滚轮守卫：控件未聚焦时吃掉滚轮事件。

    防止在 QScrollArea 里滚动页面时，鼠标悬停在 SpinBox/ComboBox 上
    误改数值。控件被点击选中（聚焦）后滚轮才生效。
    """

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Wheel and not obj.hasFocus():
            event.ignore()
            return True
        return False


def guard_wheel_children(widget: QWidget) -> None:
    """给 widget 下所有 SpinBox/ComboBox 安装 FocusWheelGuard。"""
    for w in widget.findChildren(QWidget):
        if isinstance(w, (QSpinBox, QDoubleSpinBox, QComboBox)):
            w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            w.installEventFilter(FocusWheelGuard(w))  # parent=w，随控件销毁


class FormRow(QHBoxLayout):
    """表单行：label + 任意控件。"""

    def __init__(self, label: str, widget, stretch: int = 1) -> None:
        super().__init__()
        lab = QLabel(label)
        lab.setFixedWidth(130)
        lab.setStyleSheet("color: #98989D;")
        self.addWidget(lab)
        self.addWidget(widget, stretch)
