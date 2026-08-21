"""翻译挂件测试：payload 构造、UI 状态切换、剪贴板粘贴、端到端翻译。"""
from __future__ import annotations

import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QAbstractAnimation, QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from widget.api_client import (DEFAULT_TRANSLATE_PROMPT, ChatWorker,
                               build_chat_payload, extract_reply,
                               image_to_data_url)
from widget.settings import WidgetSettings
from widget.dockable_translate_widget import (
    CIRCLE_WINDOW, DOCK_HEIGHT, DOCK_WIDTH, MODE_DOCKED, MODE_IDLE,
    MODE_PROCESSING, MODE_RESULT, RING_HOVER, STRIPE_HOVER, TranslateWidget,
)

FAKE_SERVER = Path(__file__).parent / "fake_llama_server.py"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def pump_until(cond, timeout: float = 10.0) -> bool:
    app = QApplication.instance()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def make_image(color=(200, 30, 30)) -> QImage:
    img = QImage(64, 48, QImage.Format.Format_RGB32)
    img.fill(QColor(*color))
    return img


# ---------------------------------------------------------------------
def test_payload_text_only():
    p = build_chat_payload("sys", "你好", None)
    assert p["messages"][0] == {"role": "system", "content": "sys"}
    assert p["messages"][1]["content"] == "你好"
    assert p["stream"] is False


def test_payload_with_image():
    p = build_chat_payload("sys", "翻译这张图", make_image())
    content = p["messages"][1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[1]["text"] == "翻译这张图"


def test_image_to_data_url_shrinks():
    img = QImage(4000, 3000, QImage.Format.Format_RGB32)
    img.fill(QColor(10, 20, 30))
    url = image_to_data_url(img, max_side=1568)
    # 缩小后体积应远小于原图原始数据
    assert len(url) < 4000 * 3000


def test_extract_reply():
    assert extract_reply({"choices": [{"message": {"content": " ok "}}]}) == "ok"
    with pytest.raises(ValueError):
        extract_reply({"choices": []})


# ---------------------------------------------------------------------
def test_widget_idle_and_prompt_persistence(qapp, tmp_path):
    w = TranslateWidget("http://127.0.0.1:1/v1",
                        WidgetSettings(tmp_path / "w.json"))
    assert w.mode == MODE_IDLE
    assert w.width() == CIRCLE_WINDOW and w.height() == CIRCLE_WINDOW
    assert "右键粘贴" in w.toolTip()
    w.set_system_prompt("自定义提示词")
    w2 = TranslateWidget("http://127.0.0.1:1/v1",
                         WidgetSettings(tmp_path / "w.json"))
    assert w2._system_prompt == "自定义提示词"
    w.close()
    w2.close()


def test_hover_ring_expands(qapp, tmp_path):
    w = TranslateWidget("http://127.0.0.1:1/v1",
                        WidgetSettings(tmp_path / "w.json"))
    w._animate_ring(RING_HOVER, 20)
    assert pump_until(lambda: abs(w.ring_diameter - RING_HOVER) < 0.1)
    w.close()


def test_widget_paste_text_starts_processing(qapp, tmp_path, monkeypatch):
    w = TranslateWidget("http://127.0.0.1:1/v1",
                        WidgetSettings(tmp_path / "w.json"))
    captured = []
    monkeypatch.setattr(
        w, "_begin_translation",
        lambda text, image: captured.append((text, image)) or True,
    )
    QApplication.clipboard().setText("剪贴板里的文字")
    assert w.paste_clipboard()
    assert captured == [("剪贴板里的文字", None)]
    w.close()


def test_right_click_triggers_clipboard_paste(qapp, tmp_path, monkeypatch):
    w = TranslateWidget("http://127.0.0.1:1/v1",
                        WidgetSettings(tmp_path / "w.json"))
    called = []
    monkeypatch.setattr(w, "paste_clipboard", lambda: called.append(True) or True)
    w.show()
    QTest.mouseClick(w, Qt.MouseButton.RightButton)
    qapp.processEvents()
    assert called == [True]
    w.close()


def test_widget_paste_image_starts_processing(qapp, tmp_path, monkeypatch):
    w = TranslateWidget("http://127.0.0.1:1/v1",
                        WidgetSettings(tmp_path / "w.json"))
    captured = []
    monkeypatch.setattr(
        w, "_begin_translation",
        lambda text, image: captured.append((text, image)) or True,
    )
    QApplication.clipboard().clear()
    QApplication.clipboard().setImage(make_image())
    assert w.paste_clipboard()
    assert captured[0][0] == ""
    assert isinstance(captured[0][1], QImage)
    w.close()


def test_result_expands_toward_available_space(qapp, tmp_path):
    available = qapp.primaryScreen().availableGeometry()
    w = TranslateWidget("http://127.0.0.1:1/v1",
                        WidgetSettings(tmp_path / "w.json"))
    w.move(available.left() + 40, available.top() + 100)
    w._anchor_center = w.geometry().center()
    w._show_result("Hello, world")
    assert w.mode == MODE_RESULT
    assert w.result_direction == "right"
    assert not w.result_multiline
    assert pump_until(lambda: w.width() > w.height())

    w._anchor_center = QPoint(available.right() - 40, available.top() + 140)
    w._show_result("你好，世界")
    assert w.result_direction == "left"
    w.close()


def test_long_result_expands_downward(qapp, tmp_path):
    w = TranslateWidget("http://127.0.0.1:1/v1",
                        WidgetSettings(tmp_path / "w.json"))
    w._show_result("这是一段很长的翻译结果。" * 30)
    assert w.result_multiline
    assert pump_until(lambda: w.height() > 100)
    w.close()


def test_result_text_click_copies_and_turns_gray(qapp, tmp_path):
    w = TranslateWidget("http://127.0.0.1:1/v1",
                        WidgetSettings(tmp_path / "w.json"))
    w.show()
    w._show_result("复制这段翻译")
    assert pump_until(lambda: w.mode == MODE_RESULT
                      and w._content_opacity > 0.99
                      and w._geometry_animation.state()
                      == QAbstractAnimation.State.Stopped)
    original_pos = w.pos()
    QApplication.clipboard().clear()
    QTest.mouseClick(
        w,
        Qt.MouseButton.LeftButton,
        pos=w._result_text_hit_rect().center().toPoint(),
    )
    assert QApplication.clipboard().text() == "复制这段翻译"
    assert pump_until(lambda: w.result_text_copied)
    assert w.mode == MODE_RESULT and w.pos() == original_pos
    w.close()


def test_result_frame_click_animates_back_to_orb(qapp, tmp_path):
    w = TranslateWidget("http://127.0.0.1:1/v1",
                        WidgetSettings(tmp_path / "w.json"))
    w.show()
    w._show_result("点击白圈收起")
    assert pump_until(lambda: w.mode == MODE_RESULT and w.width() > w.height())
    QTest.mouseClick(
        w,
        Qt.MouseButton.LeftButton,
        pos=QPoint(11, w.height() // 2),
    )
    assert pump_until(lambda: w._result_frame_inset >= 8.9)
    assert pump_until(lambda: w.mode == MODE_IDLE
                      and w.width() == CIRCLE_WINDOW
                      and w.height() == CIRCLE_WINDOW)
    w.close()


def test_result_state_does_not_start_drag(qapp, tmp_path):
    w = TranslateWidget("http://127.0.0.1:1/v1",
                        WidgetSettings(tmp_path / "w.json"))
    w.show()
    w._show_result("结果展示时禁止拖曳")
    assert pump_until(lambda: w.mode == MODE_RESULT and w.width() > w.height())
    QTest.mousePress(
        w,
        Qt.MouseButton.LeftButton,
        pos=w._result_text_hit_rect().center().toPoint(),
    )
    assert w._drag_offset is None
    QTest.mouseRelease(
        w,
        Qt.MouseButton.LeftButton,
        pos=w._result_text_hit_rect().center().toPoint(),
    )
    w.close()


def test_dock_handle_hover_and_click_target(qapp, tmp_path):
    settings = WidgetSettings(tmp_path / "w.json")
    w = TranslateWidget("http://127.0.0.1:1/v1", settings)
    w.dock_to("left", animate=False)
    assert w.mode == MODE_DOCKED and w.dock_edge == "left"
    assert (w.width(), w.height()) == (DOCK_WIDTH, DOCK_HEIGHT)
    w._animate_stripe(STRIPE_HOVER, 20)
    assert pump_until(lambda: abs(w.stripe_length - STRIPE_HOVER) < 0.1)
    w.show()
    QTest.mouseClick(w, Qt.MouseButton.LeftButton)
    assert pump_until(lambda: w.mode == MODE_IDLE
                      and w.width() == CIRCLE_WINDOW
                      and w.height() == CIRCLE_WINDOW)
    assert w.dock_edge is None
    w.close()


def test_widget_translate_e2e(qapp, tmp_path):
    """起假服务器，挂件走完整翻译流程并显示结果。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, str(FAKE_SERVER), "--host", "127.0.0.1",
         "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # 等服务就绪
        import urllib.request
        for _ in range(100):
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        w = TranslateWidget(f"http://127.0.0.1:{port}/v1",
                            WidgetSettings(tmp_path / "w.json"))
        assert w.translate("你好，世界")
        assert w.mode == MODE_PROCESSING
        assert pump_until(lambda: w.mode == MODE_RESULT
                          and "[译] 你好，世界" in w._result_text)
        w.close()
    finally:
        proc.terminate()
        proc.wait(timeout=5)
