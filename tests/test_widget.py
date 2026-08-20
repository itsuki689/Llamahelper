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
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from widget.api_client import (DEFAULT_TRANSLATE_PROMPT, ChatWorker,
                               build_chat_payload, extract_reply,
                               image_to_data_url)
from widget.settings import WidgetSettings
from widget.translate_widget import TranslateWidget

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
def test_widget_states_and_prompt(qapp, tmp_path):
    w = TranslateWidget("http://127.0.0.1:1/v1",
                        WidgetSettings(tmp_path / "w.json"))
    assert not w.expanded                      # 默认收起 = 翻译输入态
    assert w.collapsed_box.isVisibleTo(w)
    assert not w.expanded_box.isVisibleTo(w)
    # 默认注入翻译提示词
    assert "translation assistant" in w.prompt_edit.toPlainText()
    w.toggle_state()
    assert w.expanded and w.expanded_box.isVisibleTo(w)
    w.prompt_edit.setPlainText("自定义提示词")
    w._save_settings()
    # 重新打开应恢复提示词
    w2 = TranslateWidget("http://127.0.0.1:1/v1",
                         WidgetSettings(tmp_path / "w.json"))
    assert w2.prompt_edit.toPlainText() == "自定义提示词"
    w.close()
    w2.close()


def test_widget_paste_text(qapp, tmp_path):
    w = TranslateWidget("http://127.0.0.1:1/v1",
                        WidgetSettings(tmp_path / "w.json"))
    QApplication.clipboard().setText("剪贴板里的文字")
    w.paste_clipboard()
    assert w.quick_input.text() == "剪贴板里的文字"
    w.close()


def test_widget_paste_image_auto_expands(qapp, tmp_path):
    w = TranslateWidget("http://127.0.0.1:1/v1",
                        WidgetSettings(tmp_path / "w.json"))
    QApplication.clipboard().setImage(make_image())
    w.paste_clipboard()
    assert w.expanded                    # 图片自动展开显示缩略图
    assert w.thumb.isVisibleTo(w)
    assert w._image is not None
    w._clear_image()
    assert w._image is None and not w.thumb.isVisibleTo(w)
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
        w.quick_input.setText("你好，世界")
        w.translate()
        assert pump_until(lambda: w.expanded
                          and "[译] 你好，世界" in w.result_view.toPlainText())
        w.close()
    finally:
        proc.terminate()
        proc.wait(timeout=5)
