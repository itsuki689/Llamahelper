"""翻译挂件：无边框置顶桌面小窗，Apple 风格。

收起态（默认）：单行输入 + 翻译按钮，右键粘贴剪贴板文本/图片。
展开态：可编辑系统提示词、输入区、图片缩略图、翻译结果区。
请求在后台线程执行，结果显示在展开窗口中（收起时自动展开）。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QPlainTextEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from widget.api_client import DEFAULT_TRANSLATE_PROMPT, ChatWorker
from widget.settings import WidgetSettings

COLLAPSED_SIZE = (460, 72)
EXPANDED_SIZE = (560, 560)

WIDGET_QSS = """
QFrame#widgetRoot {
    background: #232325;
    border: 1px solid #3A3A3C;
    border-radius: 16px;
}
QFrame#dragBar { background: transparent; }
QLabel#widgetTitle { color: #98989D; font-size: 12px; font-weight: 600; }
QLabel#thumb { background: #1F1F21; border-radius: 8px; color: #98989D; }
"""


class TranslateWidget(QWidget):
    """桌面翻译挂件。close 时仅隐藏，由主程序复用实例。"""

    closed = Signal()

    def __init__(self, base_url: str, settings: WidgetSettings | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.base_url = base_url
        self.settings = settings or WidgetSettings()
        saved = self.settings.load()

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(WIDGET_QSS)

        self._expanded = False
        self._drag_pos: QPoint | None = None
        self._image: QImage | None = None
        self._worker: ChatWorker | None = None

        self._build_ui()
        self.prompt_edit.setPlainText(
            saved.get("prompt") or DEFAULT_TRANSLATE_PROMPT)
        pos = saved.get("pos")
        if isinstance(pos, list) and len(pos) == 2:
            self.move(int(pos[0]), int(pos[1]))
        else:
            # 默认放屏幕右上角附近
            geo = QGuiApplication.primaryScreen().availableGeometry()
            self.move(geo.right() - COLLAPSED_SIZE[0] - 40, geo.top() + 60)
        self._apply_state()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        root = QFrame()
        root.setObjectName("widgetRoot")
        outer.addWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(12, 8, 12, 12)
        lay.setSpacing(8)

        # ---- 标题/拖动条 ----
        bar = QFrame()
        bar.setObjectName("dragBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(4, 0, 0, 0)
        self.title_label = QLabel("翻译助手")
        self.title_label.setObjectName("widgetTitle")
        self.btn_expand = QPushButton("展开")
        self.btn_expand.setFixedWidth(56)
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedWidth(32)
        self.btn_expand.clicked.connect(self.toggle_state)
        self.btn_close.clicked.connect(self.close)
        bl.addWidget(self.title_label)
        bl.addStretch(1)
        bl.addWidget(self.btn_expand)
        bl.addWidget(self.btn_close)
        lay.addWidget(bar)
        # 拖动条与标题都可拖动窗口
        self._drag_widgets = (bar, self.title_label)

        # ---- 收起态：单行输入 ----
        self.collapsed_box = QFrame()
        cl = QHBoxLayout(self.collapsed_box)
        cl.setContentsMargins(0, 0, 0, 0)
        self.quick_input = QLineEdit()
        self.quick_input.setPlaceholderText("输入或右键粘贴文本/图片…")
        self.quick_input.returnPressed.connect(self.translate)
        self.quick_input.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.quick_input.customContextMenuRequested.connect(self._paste_menu)
        self.btn_go = QPushButton("翻译")
        self.btn_go.setObjectName("primaryBtn")
        self.btn_go.setFixedWidth(56)
        self.btn_go.clicked.connect(self.translate)
        cl.addWidget(self.quick_input, 1)
        cl.addWidget(self.btn_go)
        lay.addWidget(self.collapsed_box)

        # ---- 展开态内容 ----
        self.expanded_box = QFrame()
        el = QVBoxLayout(self.expanded_box)
        el.setContentsMargins(0, 0, 0, 0)
        el.setSpacing(8)

        el.addWidget(self._hint("系统提示词（可编辑）"))
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setFixedHeight(64)
        el.addWidget(self.prompt_edit)
        prow = QHBoxLayout()
        self.btn_reset_prompt = QPushButton("重置为翻译提示词")
        self.btn_reset_prompt.clicked.connect(
            lambda: self.prompt_edit.setPlainText(DEFAULT_TRANSLATE_PROMPT))
        prow.addWidget(self.btn_reset_prompt)
        prow.addStretch(1)
        el.addLayout(prow)

        el.addWidget(self._hint("输入（右键粘贴文本/图片）"))
        self.input_edit = QPlainTextEdit()
        self.input_edit.setFixedHeight(88)
        self.input_edit.setPlaceholderText("要翻译的内容…")
        self.input_edit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.input_edit.customContextMenuRequested.connect(self._paste_menu)
        el.addWidget(self.input_edit)

        irow = QHBoxLayout()
        self.thumb = QLabel("无图片")
        self.thumb.setObjectName("thumb")
        self.thumb.setFixedSize(96, 64)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setVisible(False)
        self.btn_clear_img = QPushButton("移除图片")
        self.btn_clear_img.setVisible(False)
        self.btn_clear_img.clicked.connect(self._clear_image)
        self.btn_paste = QPushButton("粘贴剪贴板")
        self.btn_paste.clicked.connect(self.paste_clipboard)
        irow.addWidget(self.thumb)
        irow.addWidget(self.btn_clear_img)
        irow.addStretch(1)
        irow.addWidget(self.btn_paste)
        el.addLayout(irow)

        el.addWidget(self._hint("翻译结果"))
        self.result_view = QTextEdit()
        self.result_view.setReadOnly(True)
        self.result_view.setPlaceholderText("结果将显示在这里…")
        el.addWidget(self.result_view, 1)
        lay.addWidget(self.expanded_box, 1)

    @staticmethod
    def _hint(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet("color: #98989D; font-size: 11px;")
        return lab

    # ------------------------------------------------------------------
    def set_base_url(self, url: str) -> None:
        self.base_url = url

    @property
    def expanded(self) -> bool:
        return self._expanded

    def toggle_state(self) -> None:
        self._expanded = not self._expanded
        self._apply_state()
        if self._expanded:
            self._save_settings()

    def _apply_state(self) -> None:
        self.collapsed_box.setVisible(not self._expanded)
        self.expanded_box.setVisible(self._expanded)
        self.btn_expand.setText("收起" if self._expanded else "展开")
        w, h = EXPANDED_SIZE if self._expanded else COLLAPSED_SIZE
        self.setFixedSize(w, h)

    # ------------------------------------------------------------------
    def paste_clipboard(self) -> None:
        """从剪贴板粘贴：优先文本，其次图片。"""
        md = QApplication.clipboard().mimeData()
        if md.hasText() and md.text().strip():
            self._paste_text(md.text())
        elif md.hasImage():
            self._set_image(md.imageData())
        else:
            self._set_status("剪贴板为空或内容不支持")

    def _paste_text(self, text: str) -> None:
        if self._expanded:
            self.input_edit.setPlainText(text)
        else:
            self.quick_input.setText(text)

    def _paste_menu(self, pos) -> None:
        """右键菜单：粘贴（文本/图片）。"""
        menu = QMenu(self)
        md = QApplication.clipboard().mimeData()
        act_text = menu.addAction("粘贴文本")
        act_text.setEnabled(md.hasText() and bool(md.text().strip()))
        act_img = menu.addAction("粘贴图片")
        act_img.setEnabled(md.hasImage())
        src = self.sender()
        chosen = menu.exec(src.mapToGlobal(pos))
        if chosen is act_text:
            self._paste_text(md.text())
        elif chosen is act_img:
            self._set_image(md.imageData())

    def _set_image(self, data) -> None:
        img = QImage(data) if not isinstance(data, QImage) else data
        if img.isNull():
            self._set_status("剪贴板中的图片无法识别")
            return
        self._image = img
        self.thumb.setPixmap(QPixmap.fromImage(img).scaled(
            96, 64, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        self.thumb.setVisible(True)
        self.btn_clear_img.setVisible(True)
        if not self._expanded:
            self.toggle_state()  # 图片需要展示缩略图，自动展开

    def _clear_image(self) -> None:
        self._image = None
        self.thumb.setVisible(False)
        self.thumb.setPixmap(QPixmap())
        self.btn_clear_img.setVisible(False)

    # ------------------------------------------------------------------
    def translate(self) -> None:
        text = (self.input_edit.toPlainText() if self._expanded
                else self.quick_input.text()).strip()
        if not text and self._image is None:
            self._set_status("请输入文本或粘贴图片")
            return
        if self._worker and self._worker.isRunning():
            return  # 防重复请求
        self._set_busy(True)
        self._worker = ChatWorker(self.base_url,
                                  self.prompt_edit.toPlainText().strip()
                                  or DEFAULT_TRANSLATE_PROMPT,
                                  text, self._image, parent=self)
        self._worker.result.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_result(self, text: str) -> None:
        self._set_busy(False)
        self.result_view.setPlainText(text)
        if not self._expanded:
            self.toggle_state()  # 收起时自动展开显示结果

    def _on_error(self, msg: str) -> None:
        self._set_busy(False)
        self.result_view.setPlainText(f"[翻译失败] {msg}")
        if not self._expanded:
            self.toggle_state()

    def _set_busy(self, busy: bool) -> None:
        self.btn_go.setEnabled(not busy)
        self.btn_go.setText("…" if busy else "翻译")
        self.title_label.setText("翻译助手（请求中…）" if busy else "翻译助手")

    def _set_status(self, msg: str) -> None:
        self.title_label.setText(f"翻译助手 — {msg}")

    # ------------------------------------------------------------------
    # 无边框窗口拖动
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if (e.button() == Qt.MouseButton.LeftButton
                and self._drag_widgets[0].geometry().contains(e.position().toPoint())):
            self._drag_pos = e.globalPosition().toPoint() - self.pos()
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(e)

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        self._save_settings()
        self.closed.emit()
        super().closeEvent(event)

    def _save_settings(self) -> None:
        self.settings.save({
            "prompt": self.prompt_edit.toPlainText(),
            "pos": [self.x(), self.y()],
        })
