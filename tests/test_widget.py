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
from PySide6.QtWidgets import QApplication, QAbstractItemView

from widget.api_client import (DEFAULT_TRANSLATE_PROMPT, ChatWorker,
                               build_chat_payload, extract_reply,
                               image_to_data_url)
from widget.settings import WidgetSettings
from widget.dockable_translate_widget import (
    CIRCLE_WINDOW, DOCK_HEIGHT, DOCK_WIDTH, MODE_DOCKED, MODE_IDLE,
    MODE_PROCESSING, MODE_RESULT, PromptPresetPopup, RING_HOVER,
    STRIPE_HOVER, TranslateWidget,
)
from widget.prompt_presets import (
    active_prompt_name, normalize_prompt_presets, save_prompt_presets,
)
from widget.settings_dialog import PromptSettingsDialog

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


def test_prompt_presets_migrate_and_persist(tmp_path):
    settings = WidgetSettings(tmp_path / "w.json")
    settings.save({"prompt": "旧版自定义提示词", "pos": [1, 2]})
    presets = normalize_prompt_presets(settings.load())
    assert presets == [{"name": "默认翻译", "prompt": "旧版自定义提示词"}]
    presets.append({"name": "日语翻译", "prompt": "Translate Japanese."})
    save_prompt_presets(settings, presets, "日语翻译")
    saved = settings.load()
    assert active_prompt_name(saved, presets) == "日语翻译"
    assert saved["prompt"] == "Translate Japanese."
    assert saved["pos"] == [1, 2]


def test_prompt_settings_dialog_saves_and_applies(qapp, tmp_path):
    settings = WidgetSettings(tmp_path / "w.json")
    dialog = PromptSettingsDialog(settings)
    widget = TranslateWidget("http://127.0.0.1:1/v1", settings)
    applied = []
    dialog.prompt_applied.connect(
        lambda name, prompt: applied.append((name, prompt)))
    dialog.prompt_applied.connect(widget.apply_prompt_preset)
    dialog._new_preset()
    dialog.name_edit.setText("技术文档")
    dialog.prompt_edit.setPlainText("Translate technical documentation.")
    dialog._save_current()
    assert dialog.active_name == "技术文档"
    assert applied == [("技术文档", "Translate technical documentation.")]
    assert settings.load()["active_prompt_preset"] == "技术文档"
    assert widget.active_prompt_name == "技术文档"
    assert widget._system_prompt == "Translate technical documentation."
    default_item = next(
        dialog.preset_list.item(index)
        for index in range(dialog.preset_list.count())
        if dialog.preset_list.item(index).data(Qt.ItemDataRole.UserRole)
        == "默认翻译"
    )
    dialog.preset_list.setCurrentItem(default_item)
    dialog._delete_selected()
    assert "默认翻译" not in {
        item["name"] for item in settings.load()["prompt_presets"]}
    dialog.close()
    widget.close()
    assert "默认翻译" not in {
        item["name"] for item in settings.load()["prompt_presets"]}


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


def test_result_text_hover_grays_and_recovers(qapp, tmp_path):
    w = TranslateWidget("http://127.0.0.1:1/v1",
                        WidgetSettings(tmp_path / "w.json"))
    w.show()
    w._show_result("悬停这段翻译")
    assert pump_until(lambda: w.mode == MODE_RESULT
                      and w._content_opacity > 0.99
                      and w._geometry_animation.state()
                      == QAbstractAnimation.State.Stopped)
    QTest.mouseMove(w, w._result_text_hit_rect().center().toPoint())
    assert pump_until(lambda: w._text_gray_progress > 0.99)
    QTest.mouseMove(w, QPoint(11, w.height() // 2))
    assert pump_until(lambda: w._text_gray_progress < 0.01)
    w.close()


def test_result_text_click_copies_and_flashes_frame_gray(qapp, tmp_path):
    w = TranslateWidget("http://127.0.0.1:1/v1",
                        WidgetSettings(tmp_path / "w.json"))
    w.show()
    w._show_result("复制这段翻译")
    assert pump_until(lambda: w.mode == MODE_RESULT
                      and w._geometry_animation.state()
                      == QAbstractAnimation.State.Stopped)
    QApplication.clipboard().clear()
    QTest.mouseClick(
        w,
        Qt.MouseButton.LeftButton,
        pos=w._result_text_hit_rect().center().toPoint(),
    )
    assert QApplication.clipboard().text() == "复制这段翻译"
    assert pump_until(lambda: w.result_text_copied)
    assert pump_until(lambda: not w.result_text_copied, timeout=2.0)
    assert w.mode == MODE_RESULT
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


def test_dock_right_click_only_opens_scrollable_presets(
        qapp, tmp_path, monkeypatch):
    settings = WidgetSettings(tmp_path / "w.json")
    presets = [{"name": f"预设 {i}", "prompt": f"Prompt {i}"}
               for i in range(12)]
    save_prompt_presets(settings, presets, "预设 0")
    w = TranslateWidget("http://127.0.0.1:1/v1", settings)
    w.dock_to("right", animate=False)
    w.show()
    shown = []
    pasted = []
    monkeypatch.setattr(w, "_show_preset_popup",
                        lambda pos: shown.append(pos))
    monkeypatch.setattr(w, "paste_clipboard",
                        lambda: pasted.append(True) or True)
    QTest.mouseClick(w, Qt.MouseButton.RightButton)
    assert len(shown) == 1 and not pasted
    assert w.mode == MODE_DOCKED

    popup = PromptPresetPopup(presets, "预设 0")
    assert popup.list_widget.count() == 12
    assert (popup.list_widget.verticalScrollMode()
            == QAbstractItemView.ScrollMode.ScrollPerPixel)
    popup.close()
    w.close()


def test_dock_preset_selection_flashes_stripe_gray(qapp, tmp_path):
    settings = WidgetSettings(tmp_path / "w.json")
    w = TranslateWidget("http://127.0.0.1:1/v1", settings)
    w.dock_to("left", animate=False)
    w.apply_prompt_preset("简洁翻译", "Translate concisely.", feedback=True)
    assert w.active_prompt_name == "简洁翻译"
    assert w._system_prompt == "Translate concisely."
    assert pump_until(lambda: w.preset_feedback_active)
    assert pump_until(lambda: not w.preset_feedback_active, timeout=2.0)
    assert settings.load()["active_prompt_preset"] == "简洁翻译"
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
