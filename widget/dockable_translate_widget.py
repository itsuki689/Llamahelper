"""Apple-style dockable translation orb with edge snapping."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import (
    QEasingCurve, QPoint, QRect, QRectF, Qt, QPropertyAnimation, QTimer,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QEnterEvent, QFont, QFontMetrics, QGuiApplication, QImage,
    QMouseEvent, QPainter, QPaintEvent, QPen,
)
from PySide6.QtWidgets import QApplication, QToolTip, QWidget
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QLabel, QListWidget, QListWidgetItem,
    QVBoxLayout,
)

from widget.api_client import DEFAULT_TRANSLATE_PROMPT, ChatWorker
from widget.prompt_presets import (
    active_prompt_name, normalize_prompt_presets, prompt_for_name,
    save_prompt_presets,
)
from widget.settings import WidgetSettings


MODE_IDLE = "idle"
MODE_PROCESSING = "processing"
MODE_RESULT = "result"
MODE_COLLAPSING = "collapsing"
MODE_DOCKED = "docked"

CIRCLE_WINDOW = 66
CIRCLE_BODY = 52
RING_IDLE = 36.0
RING_HOVER = 44.0
RING_PROCESSING_SMALL = 26.0
RING_PROCESSING_LARGE = 32.0
DOCK_WIDTH = 28
DOCK_HEIGHT = 92
DOCK_SCREEN_GAP = 3
DOCK_SNAP_DISTANCE = 30
STRIPE_IDLE = 24.0
STRIPE_HOVER = 50.0
PAINT_MARGIN = 6
PILL_HEIGHT = 52
MAX_RESULT_WIDTH = 420
MAX_RESULT_HEIGHT = 360
SCREEN_MARGIN = 12


@dataclass(frozen=True)
class ResultLayout:
    geometry: QRect
    direction: str
    multiline: bool
    content_height: int
    scroll_max: int


class PromptPresetPopup(QFrame):
    """Scrollable Apple-style prompt picker used by the dock handle."""

    preset_selected = Signal(str, str)

    def __init__(self, presets: list[dict[str, str]], active_name: str,
                 parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(248)
        self.setMaximumHeight(286)
        self.setStyleSheet("""
            QFrame#presetPopup {
                background: #1C1C1E;
                border: 1px solid #48484A;
                border-radius: 14px;
            }
            QLabel { color: #98989D; font-size: 11px; padding: 4px 8px; }
            QListWidget {
                background: transparent; border: none; padding: 2px;
                outline: none;
            }
            QListWidget::item {
                color: #F2F2F7; padding: 9px 10px; border-radius: 8px;
            }
            QListWidget::item:hover { background: #3A3A3C; }
            QListWidget::item:selected { background: #0A84FF; color: white; }
        """)
        self.setObjectName("presetPopup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(3)
        layout.addWidget(QLabel("选择提示词预设"))
        self.list_widget = QListWidget()
        self.list_widget.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        for preset in presets:
            label = preset["name"] + (
                "  · 当前" if preset["name"] == active_name else "")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, preset)
            self.list_widget.addItem(item)
            if preset["name"] == active_name:
                self.list_widget.setCurrentItem(item)
        self.list_widget.itemClicked.connect(self._choose)
        layout.addWidget(self.list_widget)
        visible_rows = min(max(1, len(presets)), 7)
        self.list_widget.setFixedHeight(visible_rows * 38 + 6)
        self.adjustSize()

    def _choose(self, item: QListWidgetItem) -> None:
        preset = item.data(Qt.ItemDataRole.UserRole)
        self.preset_selected.emit(preset["name"], preset["prompt"])
        self.close()


class TranslateWidget(QWidget):
    """Floating translation orb that docks to the left or right screen edge."""

    closed = Signal()
    mode_changed = Signal(str)

    def __init__(self, base_url: str, settings: WidgetSettings | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.base_url = base_url
        self.settings = settings or WidgetSettings()
        saved = self.settings.load()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setToolTip("右键粘贴并翻译 · 拖到屏幕边缘吸附")

        self._mode = MODE_IDLE
        self._dock_edge: str | None = None
        self._dock_screen_name = ""
        self._dock_offset = 0.2
        self._prompt_presets = normalize_prompt_presets(saved)
        self._active_prompt_name = active_prompt_name(saved, self._prompt_presets)
        self._system_prompt = prompt_for_name(
            self._prompt_presets, self._active_prompt_name)
        self._worker: ChatWorker | None = None
        self._image: QImage | None = None
        self._result_text = ""
        self._result_direction = "right"
        self._result_multiline = False
        self._result_content_height = 0
        self._result_scroll_offset = 0
        self._result_scroll_max = 0
        self._result_frame_inset = 5.0
        self._result_frame_width = 1.45
        self._result_frame_alpha = 232.0
        self._result_frame_gray_progress = 0.0
        self._text_gray_progress = 0.0
        self._result_text_hovered = False
        self._result_press_target: str | None = None
        self._ring_diameter = RING_IDLE
        self._stripe_length = STRIPE_IDLE
        self._stripe_gray_progress = 0.0
        self._preset_popup: PromptPresetPopup | None = None
        self._content_opacity = 0.0
        self._hovered = False
        self._drag_offset: QPoint | None = None
        self._drag_start_pos: QPoint | None = None
        self._drag_start_window_pos: QPoint | None = None
        self._drag_start_anchor: QPoint | None = None
        self._dragged = False

        self._geometry_animation: QPropertyAnimation | None = None
        self._ring_animation: QVariantAnimation | None = None
        self._stripe_animation: QVariantAnimation | None = None
        self._opacity_animation: QVariantAnimation | None = None
        self._result_frame_animation: QVariantAnimation | None = None
        self._text_color_animation: QVariantAnimation | None = None
        self._result_gray_animation: QVariantAnimation | None = None
        self._stripe_gray_animation: QVariantAnimation | None = None
        self._result_gray_timer = QTimer(self)
        self._result_gray_timer.setSingleShot(True)
        self._result_gray_timer.setInterval(1000)
        self._result_gray_timer.timeout.connect(
            lambda: self._animate_result_frame_gray(0.0))
        self._stripe_gray_timer = QTimer(self)
        self._stripe_gray_timer.setSingleShot(True)
        self._stripe_gray_timer.setInterval(1000)
        self._stripe_gray_timer.timeout.connect(
            lambda: self._animate_stripe_gray(0.0))
        self._processing_pulse = QTimer(self)
        self._processing_pulse.setInterval(460)
        self._processing_pulse.timeout.connect(self._pulse_processing_ring)

        self.resize(CIRCLE_WINDOW, CIRCLE_WINDOW)
        self._restore_geometry(saved)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def dock_edge(self) -> str | None:
        return self._dock_edge

    @property
    def result_direction(self) -> str:
        return self._result_direction

    @property
    def result_multiline(self) -> bool:
        return self._result_multiline

    @property
    def ring_diameter(self) -> float:
        return self._ring_diameter

    @property
    def stripe_length(self) -> float:
        return self._stripe_length

    @property
    def result_text_copied(self) -> bool:
        return self._result_frame_gray_progress >= 0.99

    @property
    def active_prompt_name(self) -> str:
        return self._active_prompt_name

    @property
    def preset_feedback_active(self) -> bool:
        return self._stripe_gray_progress >= 0.99

    def set_base_url(self, url: str) -> None:
        self.base_url = url

    def set_system_prompt(self, prompt: str) -> None:
        self._reload_prompt_presets()
        self._system_prompt = prompt.strip() or DEFAULT_TRANSLATE_PROMPT
        for preset in self._prompt_presets:
            if preset["name"] == self._active_prompt_name:
                preset["prompt"] = self._system_prompt
                break
        save_prompt_presets(
            self.settings, self._prompt_presets, self._active_prompt_name)

    def apply_prompt_preset(self, name: str, prompt: str,
                            feedback: bool = False) -> None:
        name = name.strip()
        prompt = prompt.strip()
        if not name or not prompt:
            return
        data = self.settings.load()
        self._prompt_presets = normalize_prompt_presets(data)
        existing = next((item for item in self._prompt_presets
                         if item["name"] == name), None)
        if existing is None:
            self._prompt_presets.append({"name": name, "prompt": prompt})
        else:
            existing["prompt"] = prompt
        self._active_prompt_name = name
        self._system_prompt = prompt
        save_prompt_presets(self.settings, self._prompt_presets, name)
        if feedback:
            self._flash_stripe_gray()

    def translate(self, text: str = "", image: QImage | None = None) -> bool:
        if not text.strip() and image is None:
            return self.paste_clipboard()
        return self._begin_translation(text, image)

    def paste_clipboard(self) -> bool:
        """Paste text (preferred) or image and immediately process it."""
        mime = QApplication.clipboard().mimeData()
        if mime.hasText() and mime.text().strip():
            return self._begin_translation(mime.text().strip(), None)
        if mime.hasImage():
            image = QImage(mime.imageData())
            if not image.isNull():
                return self._begin_translation("", image)
        QToolTip.showText(self.mapToGlobal(self.rect().center()),
                          "剪贴板为空或内容不支持", self)
        return False

    def dock_to(self, edge: str, animate: bool = True) -> None:
        if edge not in {"left", "right"}:
            raise ValueError("edge must be 'left' or 'right'")
        self._dock_to(edge, animate=animate)

    def undock(self, animate: bool = True) -> None:
        if self._mode != MODE_DOCKED:
            return
        target = self._undocked_circle_geometry()
        self._dock_edge = None
        self._set_mode(MODE_IDLE)
        self._stripe_length = STRIPE_IDLE
        self._ring_diameter = RING_IDLE
        self._content_opacity = 0.0
        self._anchor_center = target.center()
        if animate:
            self._animate_geometry(target, 300, self._save_settings)
        else:
            self.setGeometry(target)
            self._save_settings()
        self.update()

    def _begin_translation(self, text: str, image: QImage | None) -> bool:
        if self._worker is not None and self._worker.isRunning():
            return False
        if self._mode == MODE_DOCKED:
            circle_geometry = self._undocked_circle_geometry()
            self._anchor_center = circle_geometry.center()
            self._dock_edge = None
        elif self._mode == MODE_RESULT:
            circle_geometry = self._circle_geometry(self._anchor_center)
        else:
            self._anchor_center = self.geometry().center()
            circle_geometry = self._circle_geometry(self._anchor_center)

        self._image = image
        self._result_text = ""
        self._result_multiline = False
        self._result_content_height = 0
        self._result_scroll_offset = 0
        self._result_scroll_max = 0
        self._reset_result_feedback()
        self._content_opacity = 0.0
        self._processing_pulse.stop()
        self._set_mode(MODE_PROCESSING)
        self._animate_geometry(circle_geometry, 260)
        self._animate_ring(RING_PROCESSING_SMALL, 210)
        self._processing_pulse.start()
        self._worker = ChatWorker(
            self.base_url, self._system_prompt, text, image, parent=self)
        self._worker.result.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.start()
        return True

    def _on_result(self, text: str) -> None:
        self._show_result(text)

    def _on_error(self, message: str) -> None:
        self._show_result(f"翻译失败：{message}")

    def _show_result(self, text: str) -> None:
        self._processing_pulse.stop()
        self._result_text = text.strip() or "模型返回了空内容"
        self._reset_result_feedback()
        layout = self._calculate_result_layout(self._result_text)
        self._result_direction = layout.direction
        self._result_multiline = layout.multiline
        self._result_content_height = layout.content_height
        self._result_scroll_offset = 0
        self._result_scroll_max = layout.scroll_max
        self._set_mode(MODE_RESULT)
        self._ring_diameter = RING_IDLE
        self._animate_geometry(layout.geometry, 360, self._save_settings)
        self._animate_content_opacity(1.0, 320)

    def _reset_result_feedback(self) -> None:
        if self._result_frame_animation is not None:
            self._result_frame_animation.stop()
        if self._text_color_animation is not None:
            self._text_color_animation.stop()
        if self._result_gray_animation is not None:
            self._result_gray_animation.stop()
        self._result_gray_timer.stop()
        self._result_frame_inset = 5.0
        self._result_frame_width = 1.45
        self._result_frame_alpha = 232.0
        self._result_frame_gray_progress = 0.0
        self._text_gray_progress = 0.0
        self._result_text_hovered = False
        self._result_press_target = None

    def _copy_result_to_clipboard(self) -> None:
        if self._mode != MODE_RESULT or not self._result_text:
            return
        QApplication.clipboard().setText(self._result_text)
        self._flash_result_frame_gray()

    def _animate_text_hover(self, hovered: bool) -> None:
        if self._result_text_hovered == hovered:
            return
        self._result_text_hovered = hovered
        if self._text_color_animation is not None:
            self._text_color_animation.stop()
        animation = QVariantAnimation(self)
        animation.setDuration(150)
        animation.setStartValue(self._text_gray_progress)
        animation.setEndValue(1.0 if hovered else 0.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(self._set_text_gray_progress)
        self._text_color_animation = animation
        animation.start()

    def _flash_result_frame_gray(self) -> None:
        self._result_gray_timer.stop()
        if self._result_gray_animation is not None:
            self._result_gray_animation.stop()
        self._set_result_frame_gray_progress(1.0)
        self._result_gray_timer.start()

    def _animate_result_frame_gray(self, target: float,
                                   duration: int = 180) -> None:
        if self._result_gray_animation is not None:
            self._result_gray_animation.stop()
        animation = QVariantAnimation(self)
        animation.setDuration(duration)
        animation.setStartValue(self._result_frame_gray_progress)
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(self._set_result_frame_gray_progress)
        self._result_gray_animation = animation
        animation.start()

    def _flash_stripe_gray(self) -> None:
        self._stripe_gray_timer.stop()
        if self._stripe_gray_animation is not None:
            self._stripe_gray_animation.stop()
        self._set_stripe_gray_progress(1.0)
        self._stripe_gray_timer.start()

    def _animate_stripe_gray(self, target: float, duration: int = 180) -> None:
        if self._stripe_gray_animation is not None:
            self._stripe_gray_animation.stop()
        animation = QVariantAnimation(self)
        animation.setDuration(duration)
        animation.setStartValue(self._stripe_gray_progress)
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(self._set_stripe_gray_progress)
        self._stripe_gray_animation = animation
        animation.start()

    def _press_result_frame_and_collapse(self) -> None:
        if self._mode != MODE_RESULT:
            return
        if self._result_frame_animation is not None:
            self._result_frame_animation.stop()
        animation = QVariantAnimation(self)
        animation.setDuration(125)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(self._set_result_frame_press_progress)
        animation.finished.connect(self._collapse_result_to_orb)
        self._result_frame_animation = animation
        animation.start()

    def _collapse_result_to_orb(self) -> None:
        if self._mode != MODE_RESULT:
            return
        self._set_mode(MODE_COLLAPSING)
        self._result_multiline = False
        self._animate_content_opacity(0.0, 110)
        self._animate_geometry(
            self._circle_geometry(self._anchor_center),
            330,
            self._finish_result_collapse,
        )

    def _finish_result_collapse(self) -> None:
        self._result_text = ""
        self._result_content_height = 0
        self._result_scroll_offset = 0
        self._result_scroll_max = 0
        self._content_opacity = 0.0
        self._ring_diameter = RING_IDLE
        self._reset_result_feedback()
        self._set_mode(MODE_IDLE)
        self.unsetCursor()
        self._save_settings()

    def _pulse_processing_ring(self) -> None:
        target = (RING_PROCESSING_LARGE
                  if self._ring_diameter <= RING_PROCESSING_SMALL + 1
                  else RING_PROCESSING_SMALL)
        self._animate_ring(target, 360)

    def _calculate_result_layout(self, text: str) -> ResultLayout:
        anchor = self._anchor_center
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        available = screen.availableGeometry()
        left_space = anchor.x() - available.left()
        right_space = available.right() - anchor.x()
        direction = "right" if right_space >= left_space else "left"
        side_space = right_space if direction == "right" else left_space

        metrics = QFontMetrics(self._result_font())
        single_width = metrics.horizontalAdvance(text.replace("\n", " "))
        max_body_width = max(
            170,
            min(MAX_RESULT_WIDTH,
                int(side_space + CIRCLE_BODY / 2 - SCREEN_MARGIN)),
        )
        multiline = "\n" in text or single_width + 38 > max_body_width
        if not multiline:
            body_width = max(CIRCLE_BODY, single_width + 38)
            body_height = PILL_HEIGHT
            body_top = anchor.y() - body_height // 2
            content_height = metrics.height()
            scroll_max = 0
        else:
            body_width = max(190, max_body_width)
            text_width = max(80, body_width - 36)
            bounds = metrics.boundingRect(
                QRect(0, 0, text_width, 100000),
                Qt.TextFlag.TextWordWrap,
                text,
            )
            room_below = available.bottom() - (anchor.y() - CIRCLE_BODY // 2)
            max_height = max(96, min(MAX_RESULT_HEIGHT,
                                      room_below - SCREEN_MARGIN))
            if bounds.height() + 34 > max_height:
                text_width = max(80, body_width - 44)
                bounds = metrics.boundingRect(
                    QRect(0, 0, text_width, 100000),
                    Qt.TextFlag.TextWordWrap,
                    text,
                )
            body_height = min(max(96, bounds.height() + 34), max_height)
            body_top = anchor.y() - CIRCLE_BODY // 2
            content_height = max(metrics.height(), bounds.height())
            scroll_max = max(0, content_height - max(1, body_height - 26))

        if direction == "right":
            body_left = anchor.x() - CIRCLE_BODY // 2
        else:
            body_left = anchor.x() + CIRCLE_BODY // 2 - body_width
        body_left = max(
            available.left() + SCREEN_MARGIN,
            min(body_left, available.right() - SCREEN_MARGIN - body_width + 1),
        )
        body_top = max(
            available.top() + SCREEN_MARGIN,
            min(body_top, available.bottom() - SCREEN_MARGIN - body_height + 1),
        )
        geometry = QRect(
            body_left - PAINT_MARGIN, body_top - PAINT_MARGIN,
            body_width + PAINT_MARGIN * 2,
            body_height + PAINT_MARGIN * 2,
        )
        return ResultLayout(
            geometry, direction, multiline, content_height, scroll_max)

    @staticmethod
    def _circle_geometry(center: QPoint) -> QRect:
        return QRect(center.x() - CIRCLE_WINDOW // 2,
                     center.y() - CIRCLE_WINDOW // 2,
                     CIRCLE_WINDOW, CIRCLE_WINDOW)

    def _snap_if_near_edge(self, global_pos: QPoint) -> bool:
        screen = QGuiApplication.screenAt(global_pos) or self._screen_for_widget()
        available = screen.availableGeometry()
        left_distance = abs(global_pos.x() - available.left())
        right_distance = abs(global_pos.x() - available.right())
        if min(left_distance, right_distance) > DOCK_SNAP_DISTANCE:
            return False
        self._dock_screen_name = screen.name()
        self._dock_to(
            "left" if left_distance <= right_distance else "right",
            screen=screen,
        )
        return True

    def _dock_to(self, edge: str, animate: bool = True, screen=None) -> None:
        screen = screen or self._screen_for_widget()
        available = screen.availableGeometry()
        center_y = self.geometry().center().y()
        usable = max(1, available.height() - DOCK_HEIGHT)
        self._dock_offset = max(
            0.0,
            min(1.0, (center_y - available.top() - DOCK_HEIGHT / 2) / usable),
        )
        self._dock_edge = edge
        self._dock_screen_name = screen.name()
        target = self._dock_geometry(screen, edge, self._dock_offset)
        self._content_opacity = 0.0
        self._processing_pulse.stop()
        self._set_mode(MODE_DOCKED)
        self._animate_stripe(STRIPE_IDLE, 160)
        if animate:
            self._animate_geometry(target, 310, self._save_settings)
        else:
            self.setGeometry(target)
            self._save_settings()
        self.update()

    def _reload_prompt_presets(self) -> None:
        data = self.settings.load()
        self._prompt_presets = normalize_prompt_presets(data)
        self._active_prompt_name = active_prompt_name(
            data, self._prompt_presets)
        self._system_prompt = prompt_for_name(
            self._prompt_presets, self._active_prompt_name)

    def _show_preset_popup(self, global_pos: QPoint) -> None:
        if self._mode != MODE_DOCKED:
            return
        self._reload_prompt_presets()
        popup = PromptPresetPopup(
            self._prompt_presets, self._active_prompt_name, self)
        popup.preset_selected.connect(
            lambda name, prompt: self.apply_prompt_preset(
                name, prompt, feedback=True))
        popup.destroyed.connect(lambda: setattr(self, "_preset_popup", None))
        self._preset_popup = popup
        popup.adjustSize()
        screen = QGuiApplication.screenAt(global_pos) or self._screen_for_widget()
        available = screen.availableGeometry()
        if self._dock_edge == "right":
            x = self.geometry().left() - popup.width() - 8
        else:
            x = self.geometry().right() + 8
        y = global_pos.y() - popup.height() // 2
        x = max(available.left() + 8,
                min(x, available.right() - popup.width() - 7))
        y = max(available.top() + 8,
                min(y, available.bottom() - popup.height() - 7))
        popup.move(x, y)
        popup.show()
        popup.raise_()

    @staticmethod
    def _dock_geometry(screen, edge: str, offset: float) -> QRect:
        available = screen.availableGeometry()
        usable = max(0, available.height() - DOCK_HEIGHT)
        y = available.top() + round(usable * max(0.0, min(1.0, offset)))
        x = (available.left() + DOCK_SCREEN_GAP
             if edge == "left"
             else available.right() - DOCK_WIDTH - DOCK_SCREEN_GAP + 1)
        return QRect(x, y, DOCK_WIDTH, DOCK_HEIGHT)

    def _undocked_circle_geometry(self) -> QRect:
        screen = self._screen_for_widget()
        available = screen.availableGeometry()
        center_y = self.geometry().center().y()
        y = max(
            available.top() + SCREEN_MARGIN,
            min(center_y - CIRCLE_WINDOW // 2,
                available.bottom() - SCREEN_MARGIN - CIRCLE_WINDOW + 1),
        )
        if self._dock_edge == "left":
            x = available.left() + DOCK_WIDTH + 12
        else:
            x = available.right() - DOCK_WIDTH - 12 - CIRCLE_WINDOW + 1
        return QRect(x, y, CIRCLE_WINDOW, CIRCLE_WINDOW)

    def _screen_for_widget(self):
        screen = QGuiApplication.screenAt(self.geometry().center())
        if screen is not None:
            return screen
        if self._dock_screen_name:
            for candidate in QGuiApplication.screens():
                if candidate.name() == self._dock_screen_name:
                    return candidate
        return QGuiApplication.primaryScreen()

    def _animate_geometry(self, target: QRect, duration: int,
                          finished=None) -> None:
        if self._geometry_animation is not None:
            self._geometry_animation.stop()
        animation = QPropertyAnimation(self, b"geometry", self)
        animation.setDuration(duration)
        animation.setStartValue(self.geometry())
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        if finished is not None:
            animation.finished.connect(finished)
        self._geometry_animation = animation
        animation.start()

    def _animate_ring(self, target: float, duration: int = 190) -> None:
        if self._ring_animation is not None:
            self._ring_animation.stop()
        animation = QVariantAnimation(self)
        animation.setDuration(duration)
        animation.setStartValue(self._ring_diameter)
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(self._set_ring_diameter)
        self._ring_animation = animation
        animation.start()

    def _animate_stripe(self, target: float, duration: int = 190) -> None:
        if self._stripe_animation is not None:
            self._stripe_animation.stop()
        animation = QVariantAnimation(self)
        animation.setDuration(duration)
        animation.setStartValue(self._stripe_length)
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(self._set_stripe_length)
        self._stripe_animation = animation
        animation.start()

    def _animate_content_opacity(self, target: float, duration: int) -> None:
        if self._opacity_animation is not None:
            self._opacity_animation.stop()
        animation = QVariantAnimation(self)
        animation.setDuration(duration)
        animation.setStartValue(self._content_opacity)
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(self._set_content_opacity)
        self._opacity_animation = animation
        animation.start()

    def _set_ring_diameter(self, value) -> None:
        self._ring_diameter = float(value)
        self.update()

    def _set_stripe_length(self, value) -> None:
        self._stripe_length = float(value)
        self.update()

    def _set_content_opacity(self, value) -> None:
        self._content_opacity = float(value)
        self.update()

    def _set_text_gray_progress(self, value) -> None:
        self._text_gray_progress = float(value)
        self.update()

    def _set_result_frame_gray_progress(self, value) -> None:
        self._result_frame_gray_progress = float(value)
        self.update()

    def _set_stripe_gray_progress(self, value) -> None:
        self._stripe_gray_progress = float(value)
        self.update()

    def _set_result_frame_press_progress(self, value) -> None:
        progress = float(value)
        self._result_frame_inset = 5.0 + 4.0 * progress
        self._result_frame_width = 1.45 + 1.55 * progress
        self._result_frame_alpha = 232.0 + 23.0 * progress
        self.update()

    def _set_mode(self, mode: str) -> None:
        if self._mode != mode:
            self._mode = mode
            self.mode_changed.emit(mode)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._mode in {MODE_IDLE, MODE_PROCESSING}:
            self._paint_orb(painter)
        elif self._mode == MODE_DOCKED:
            self._paint_dock(painter)
        else:
            self._paint_result(painter)

    def _paint_orb(self, painter: QPainter) -> None:
        center = QRectF(self.rect()).center()
        body = QRectF(center.x() - CIRCLE_BODY / 2,
                      center.y() - CIRCLE_BODY / 2,
                      CIRCLE_BODY, CIRCLE_BODY)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#111113"))
        painter.drawEllipse(body)
        edge_pen = QPen(QColor(255, 255, 255, 115), 0.85)
        edge_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(edge_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(body.adjusted(0.5, 0.5, -0.5, -0.5))

        diameter = self._ring_diameter
        ring = QRectF(center.x() - diameter / 2,
                      center.y() - diameter / 2, diameter, diameter)
        pen = QPen(QColor(255, 255, 255, 238), 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(ring)

    def _paint_dock(self, painter: QPainter) -> None:
        body = QRectF(self.rect()).adjusted(2.5, 2.5, -2.5, -2.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#111113"))
        painter.drawRoundedRect(body, body.width() / 2, body.width() / 2)
        painter.setPen(QPen(QColor(255, 255, 255, 55), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(body, body.width() / 2, body.width() / 2)
        center = body.center()
        stripe_gray = self._stripe_gray_progress
        stripe = QPen(QColor(
            round(255 + (142 - 255) * stripe_gray),
            round(255 + (142 - 255) * stripe_gray),
            round(255 + (147 - 255) * stripe_gray),
            235,
        ), 2.0)
        stripe.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(stripe)
        half = min(self._stripe_length, body.height() - 18) / 2
        painter.drawLine(QPoint(round(center.x()), round(center.y() - half)),
                         QPoint(round(center.x()), round(center.y() + half)))

    def _paint_result(self, painter: QPainter) -> None:
        body = QRectF(self.rect()).adjusted(
            PAINT_MARGIN, PAINT_MARGIN, -PAINT_MARGIN, -PAINT_MARGIN)
        radius = body.height() / 2 if not self._result_multiline else 18.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#111113"))
        painter.drawRoundedRect(body, radius, radius)
        painter.setPen(QPen(QColor(255, 255, 255, 105), 0.85))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(body, radius, radius)

        inset = self._result_frame_inset
        inner = body.adjusted(inset, inset, -inset, -inset)
        inner_radius = inner.height() / 2 if not self._result_multiline else 13.0
        frame_gray = self._result_frame_gray_progress
        painter.setPen(QPen(QColor(
            round(255 + (142 - 255) * frame_gray),
            round(255 + (142 - 255) * frame_gray),
            round(255 + (147 - 255) * frame_gray),
            round(self._result_frame_alpha),
        ), self._result_frame_width))
        painter.drawRoundedRect(inner, inner_radius, inner_radius)
        alpha = max(0, min(255, round(255 * self._content_opacity)))
        gray = self._text_gray_progress
        painter.setPen(QColor(
            round(247 + (142 - 247) * gray),
            round(247 + (142 - 247) * gray),
            round(250 + (147 - 250) * gray),
            alpha,
        ))
        painter.setFont(self._result_font())
        text_rect = self._result_text_area()
        if not self._result_multiline:
            flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            painter.drawText(text_rect, int(flags), self._result_text)
        else:
            painter.save()
            painter.setClipRect(text_rect)
            draw_rect = QRectF(
                text_rect.left(),
                text_rect.top() - self._result_scroll_offset,
                text_rect.width(),
                max(text_rect.height(), self._result_content_height),
            )
            flags = (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                     | Qt.TextFlag.TextWordWrap)
            painter.drawText(draw_rect, int(flags), self._result_text)
            painter.restore()

        if self._result_scroll_max > 0:
            track = QRectF(
                body.right() - 12,
                body.top() + 18,
                2.5,
                max(24.0, body.height() - 36),
            )
            visible_ratio = min(
                1.0, text_rect.height() / max(1, self._result_content_height))
            thumb_height = max(24.0, track.height() * visible_ratio)
            travel = max(0.0, track.height() - thumb_height)
            progress = self._result_scroll_offset / self._result_scroll_max
            thumb = QRectF(
                track.left(), track.top() + travel * progress,
                track.width(), thumb_height,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, round(46 * self._content_opacity)))
            painter.drawRoundedRect(track, track.width() / 2, track.width() / 2)
            painter.setBrush(QColor(255, 255, 255, round(170 * self._content_opacity)))
            painter.drawRoundedRect(thumb, thumb.width() / 2, thumb.width() / 2)

    def _result_body_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(
            PAINT_MARGIN, PAINT_MARGIN, -PAINT_MARGIN, -PAINT_MARGIN)

    def _result_text_area(self) -> QRectF:
        right_margin = 26 if self._result_scroll_max > 0 else 18
        return self._result_body_rect().adjusted(18, 13, -right_margin, -13)

    def _result_text_hit_rect(self) -> QRectF:
        """Return the visible glyph area's forgiving click target."""
        area = self._result_text_area()
        metrics = QFontMetrics(self._result_font())
        if not self._result_multiline:
            width = min(area.width(), metrics.horizontalAdvance(self._result_text))
            height = min(area.height(), metrics.height())
            hit = QRectF(
                area.left(), area.center().y() - height / 2,
                width, height,
            )
        else:
            if self._result_scroll_max > 0:
                return area.adjusted(-4, -4, 4, 4)
            bounds = metrics.boundingRect(
                QRect(0, 0, max(1, int(area.width())),
                      max(1, int(area.height()))),
                Qt.TextFlag.TextWordWrap,
                self._result_text,
            )
            hit = QRectF(
                area.left(), area.top(),
                min(area.width(), bounds.width()),
                min(area.height(), bounds.height()),
            )
        return hit.adjusted(-4, -4, 4, 4)

    def wheelEvent(self, event) -> None:
        if self._mode == MODE_RESULT and self._result_scroll_max > 0:
            delta = event.pixelDelta().y()
            if not delta:
                delta = round(event.angleDelta().y() / 120 * 42)
            if delta:
                self._result_scroll_offset = max(
                    0,
                    min(self._result_scroll_max,
                        self._result_scroll_offset - delta),
                )
                self.update()
            event.accept()
            return
        super().wheelEvent(event)

    @staticmethod
    def _result_font() -> QFont:
        font = QFont("Segoe UI")
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.Medium)
        return font

    def enterEvent(self, event: QEnterEvent) -> None:
        self._hovered = True
        if self._mode == MODE_IDLE:
            self._animate_ring(RING_HOVER)
        elif self._mode == MODE_DOCKED:
            self._animate_stripe(STRIPE_HOVER)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.unsetCursor()
        if self._mode == MODE_RESULT:
            self._animate_text_hover(False)
        if self._mode == MODE_IDLE:
            self._animate_ring(RING_IDLE)
        elif self._mode == MODE_DOCKED:
            self._animate_stripe(STRIPE_IDLE)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._mode == MODE_RESULT:
                self._result_press_target = (
                    "text" if self._result_text_hit_rect().contains(event.position())
                    else "frame"
                )
                self._drag_offset = None
                self._dragged = False
                event.accept()
                return
            if self._mode == MODE_COLLAPSING:
                event.accept()
                return
            self._drag_start_pos = event.globalPosition().toPoint()
            self._dragged = False
            if self._mode != MODE_DOCKED:
                self._drag_offset = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft())
                self._drag_start_window_pos = self.pos()
                self._drag_start_anchor = self._anchor_center
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._mode == MODE_RESULT and not event.buttons():
            self._animate_text_hover(
                self._result_text_hit_rect().contains(event.position()))
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            event.accept()
            return
        if (self._mode != MODE_DOCKED and self._drag_offset is not None
                and event.buttons() & Qt.MouseButton.LeftButton):
            global_pos = event.globalPosition().toPoint()
            if self._drag_start_pos is not None:
                self._dragged = ((global_pos - self._drag_start_pos).manhattanLength()
                                 >= QApplication.startDragDistance())
            new_pos = global_pos - self._drag_offset
            self.move(new_pos)
            if (self._drag_start_window_pos is not None
                    and self._drag_start_anchor is not None):
                self._anchor_center = (self._drag_start_anchor + new_pos
                                       - self._drag_start_window_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            if self._mode == MODE_DOCKED:
                self._show_preset_popup(event.globalPosition().toPoint())
            elif self._mode != MODE_COLLAPSING:
                self.paste_clipboard()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if self._mode == MODE_RESULT:
                over_text = self._result_text_hit_rect().contains(event.position())
                if self._result_press_target == "text" and over_text:
                    self._copy_result_to_clipboard()
                elif self._result_press_target == "frame" and not over_text:
                    self._press_result_frame_and_collapse()
                self._result_press_target = None
                event.accept()
                return
            if self._mode == MODE_COLLAPSING:
                event.accept()
                return
            if self._mode == MODE_DOCKED:
                self.undock()
            elif self._dragged:
                if not self._snap_if_near_edge(event.globalPosition().toPoint()):
                    self._save_settings()
            self._drag_offset = None
            self._drag_start_pos = None
            self._drag_start_window_pos = None
            self._drag_start_anchor = None
            self._dragged = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _restore_geometry(self, saved: dict) -> None:
        self._anchor_center = QPoint(0, 0)
        if (saved.get("mode") == MODE_DOCKED
                and saved.get("edge") in {"left", "right"}):
            screen_name = str(saved.get("screen") or "")
            screen = next((s for s in QGuiApplication.screens()
                           if s.name() == screen_name),
                          QGuiApplication.primaryScreen())
            self._dock_edge = str(saved["edge"])
            self._dock_screen_name = screen.name()
            self._dock_offset = float(saved.get("edge_offset", 0.2))
            self._mode = MODE_DOCKED
            self.setGeometry(self._dock_geometry(
                screen, self._dock_edge, self._dock_offset))
            self._anchor_center = self.geometry().center()
            return
        pos = saved.get("pos")
        if isinstance(pos, list) and len(pos) == 2:
            self.move(int(pos[0]), int(pos[1]))
        else:
            available = QGuiApplication.primaryScreen().availableGeometry()
            self.move(available.right() - CIRCLE_WINDOW - 42,
                      available.top() + 72)
        self._anchor_center = self.geometry().center()

    def _save_settings(self) -> None:
        if self._mode == MODE_DOCKED:
            pos = [self.x(), self.y()]
        else:
            circle = self._circle_geometry(self._anchor_center)
            pos = [circle.x(), circle.y()]
        data = self.settings.load()
        data.update({
            "pos": pos,
            "mode": MODE_DOCKED if self._mode == MODE_DOCKED else MODE_IDLE,
            "edge": self._dock_edge,
            "screen": self._dock_screen_name,
            "edge_offset": self._dock_offset,
        })
        self.settings.save(data)

    def closeEvent(self, event) -> None:
        self._processing_pulse.stop()
        self._save_settings()
        self.closed.emit()
        super().closeEvent(event)
