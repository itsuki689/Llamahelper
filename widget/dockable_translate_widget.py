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

from widget.api_client import DEFAULT_TRANSLATE_PROMPT, ChatWorker
from widget.settings import WidgetSettings


MODE_IDLE = "idle"
MODE_PROCESSING = "processing"
MODE_RESULT = "result"
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
        self._system_prompt = saved.get("prompt") or DEFAULT_TRANSLATE_PROMPT
        self._worker: ChatWorker | None = None
        self._image: QImage | None = None
        self._result_text = ""
        self._result_direction = "right"
        self._result_multiline = False
        self._ring_diameter = RING_IDLE
        self._stripe_length = STRIPE_IDLE
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

    def set_base_url(self, url: str) -> None:
        self.base_url = url

    def set_system_prompt(self, prompt: str) -> None:
        self._system_prompt = prompt.strip() or DEFAULT_TRANSLATE_PROMPT
        self._save_settings()

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
        layout = self._calculate_result_layout(self._result_text)
        self._result_direction = layout.direction
        self._result_multiline = layout.multiline
        self._set_mode(MODE_RESULT)
        self._ring_diameter = RING_IDLE
        self._animate_geometry(layout.geometry, 360, self._save_settings)
        self._animate_content_opacity(1.0, 320)

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
        else:
            body_width = max(190, max_body_width)
            text_width = max(80, body_width - 36)
            bounds = metrics.boundingRect(
                QRect(0, 0, text_width, 2000),
                Qt.TextFlag.TextWordWrap,
                text,
            )
            room_below = available.bottom() - (anchor.y() - CIRCLE_BODY // 2)
            max_height = max(96, min(MAX_RESULT_HEIGHT,
                                      room_below - SCREEN_MARGIN))
            body_height = min(max(96, bounds.height() + 34), max_height)
            body_top = anchor.y() - CIRCLE_BODY // 2

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
        return ResultLayout(geometry, direction, multiline)

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
        painter.setBrush(QColor(0, 0, 0, 90))
        painter.drawEllipse(body.translated(0, 2).adjusted(-2, -2, 2, 2))
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
        painter.setBrush(QColor(0, 0, 0, 90))
        painter.drawRoundedRect(body.translated(0, 1.5),
                                body.width() / 2, body.width() / 2)
        painter.setBrush(QColor("#111113"))
        painter.drawRoundedRect(body, body.width() / 2, body.width() / 2)
        painter.setPen(QPen(QColor(255, 255, 255, 55), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(body, body.width() / 2, body.width() / 2)
        center = body.center()
        stripe = QPen(QColor(255, 255, 255, 235), 2.0)
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
        painter.setBrush(QColor(0, 0, 0, 100))
        painter.drawRoundedRect(body.translated(0, 2).adjusted(-1, -1, 1, 1),
                                radius, radius)
        painter.setBrush(QColor("#111113"))
        painter.drawRoundedRect(body, radius, radius)
        painter.setPen(QPen(QColor(255, 255, 255, 105), 0.85))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(body, radius, radius)

        inner = body.adjusted(5, 5, -5, -5)
        inner_radius = inner.height() / 2 if not self._result_multiline else 13.0
        painter.setPen(QPen(QColor(255, 255, 255, 232), 1.45))
        painter.drawRoundedRect(inner, inner_radius, inner_radius)
        alpha = max(0, min(255, round(255 * self._content_opacity)))
        painter.setPen(QColor(247, 247, 250, alpha))
        painter.setFont(self._result_font())
        text_rect = body.adjusted(18, 13, -18, -13)
        flags = (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                 if not self._result_multiline
                 else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                 | Qt.TextFlag.TextWordWrap)
        painter.drawText(text_rect, int(flags), self._result_text)

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
        if self._mode == MODE_IDLE:
            self._animate_ring(RING_IDLE)
        elif self._mode == MODE_DOCKED:
            self._animate_stripe(STRIPE_IDLE)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
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
            self.paste_clipboard()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
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
        self.settings.save({
            "prompt": self._system_prompt,
            "pos": pos,
            "mode": MODE_DOCKED if self._mode == MODE_DOCKED else MODE_IDLE,
            "edge": self._dock_edge,
            "screen": self._dock_screen_name,
            "edge_offset": self._dock_offset,
        })

    def closeEvent(self, event) -> None:
        self._processing_pulse.stop()
        self._save_settings()
        self.closed.emit()
        super().closeEvent(event)
