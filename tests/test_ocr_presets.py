"""OCR preset migration, editing and image request regressions."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from ui.pages.translation_widget_page import TranslationWidgetPage
from widget.api_client import (
    DEFAULT_OCR_PROMPT, IMAGE_CHINESE_REQUIREMENT, IMAGE_MODE_OCR,
    IMAGE_MODE_TRANSLATE, IMAGE_OCR_REQUEST_TEXT, build_chat_payload,
)
from widget.dockable_translate_widget import MODE_RESULT, TranslateWidget
from widget.prompt_presets import (
    OCR_PRESET_NAME, load_prompt_settings, save_prompt_presets,
)
from widget.settings import WidgetSettings


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("legacy", [True, False])
def test_ocr_migration_preserves_custom_settings(tmp_path, legacy):
    settings = WidgetSettings(tmp_path / "widget.json")
    original = {"prompt": "My custom prompt", "pos": [123, 456]}
    if not legacy:
        original.update(prompt_presets=[
            {"name": "First", "prompt": "First prompt"},
            {"name": OCR_PRESET_NAME, "prompt": "My custom prompt"},
        ], active_prompt_preset=OCR_PRESET_NAME)
    settings.save(original)
    data = load_prompt_settings(settings)
    assert data["prompt"] == original["prompt"]
    assert data["pos"] == original["pos"]
    assert data["active_prompt_preset"] == (
        "默认翻译" if legacy else OCR_PRESET_NAME)
    ocr = data["prompt_presets"][-1]
    assert ocr["image_mode"] == IMAGE_MODE_OCR
    assert ocr["prompt"] == DEFAULT_OCR_PROMPT
    assert ocr["name"] == (OCR_PRESET_NAME if legacy else OCR_PRESET_NAME + " 2")
    assert load_prompt_settings(settings) == data


def test_deleted_ocr_preset_is_not_reinstalled(tmp_path):
    settings = WidgetSettings(tmp_path / "widget.json")
    data = load_prompt_settings(settings)
    remaining = [p for p in data["prompt_presets"] if p["name"] != OCR_PRESET_NAME]
    save_prompt_presets(settings, remaining, remaining[0]["name"])
    assert load_prompt_settings(settings)["prompt_presets"] == remaining


@pytest.mark.parametrize("retry", [False, True])
def test_ocr_payload_preserves_original_language(retry):
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    payload = build_chat_payload(DEFAULT_OCR_PROMPT, "", image,
                                 image_retry=retry, image_mode=IMAGE_MODE_OCR)
    assert payload["messages"][0]["content"] == DEFAULT_OCR_PROMPT
    content = payload["messages"][1]["content"]
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[-1]["text"] == IMAGE_OCR_REQUEST_TEXT
    assert IMAGE_CHINESE_REQUIREMENT not in json.dumps(payload, ensure_ascii=False)


def test_ocr_page_edit_and_mode_persistence(qapp, tmp_path):
    settings = WidgetSettings(tmp_path / "widget.json")
    page = TranslationWidgetPage(settings)
    widget = TranslateWidget("http://127.0.0.1:1/v1", settings)
    page.prompt_applied.connect(widget.apply_prompt_preset)
    try:
        item = next(page.preset_list.item(i) for i in range(page.preset_list.count())
                    if page.preset_list.item(i).data(Qt.ItemDataRole.UserRole) == OCR_PRESET_NAME)
        page.preset_list.setCurrentItem(item)
        assert page.image_mode_combo.currentData() == IMAGE_MODE_OCR
        page.name_edit.setText("原文提取")
        page.prompt_edit.setPlainText("Recognize text exactly.")
        page._save_current()
        assert widget._image_mode == IMAGE_MODE_OCR
        assert widget._system_prompt == "Recognize text exactly."
        page.btn_reset.click()
        assert page.prompt_edit.toPlainText() == DEFAULT_OCR_PROMPT
        page.reload()
        assert page.image_mode_combo.currentData() == IMAGE_MODE_OCR
        assert page.prompt_edit.toPlainText() == "Recognize text exactly."
        # Switching the image mode must also persist through the existing signal.
        page.image_mode_combo.setCurrentIndex(0)
        page._save_current()
        assert widget._image_mode == IMAGE_MODE_TRANSLATE
        assert "image_mode" not in settings.load()["prompt_presets"][-1]
    finally:
        page.close()
        widget.close()


def test_ocr_widget_image_request_keeps_english_without_retry(qapp, tmp_path):
    requests = []
    recognized = "Connection Problem Detected\nPlease reload the page."

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"choices": [{"message": {"content": recognized}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = WidgetSettings(tmp_path / "widget.json")
    widget = TranslateWidget(f"http://127.0.0.1:{server.server_port}/v1", settings)
    try:
        # A persisted selection from the settings page is reloaded before a request.
        data = settings.load()
        save_prompt_presets(settings, data["prompt_presets"], OCR_PRESET_NAME)
        picture = QImage(32, 32, QImage.Format.Format_RGB32)
        picture.fill(Qt.GlobalColor.white)
        assert widget._begin_translation("", picture)
        deadline = time.monotonic() + 5
        while widget._worker is not None and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert widget._worker is None
        assert widget.mode == MODE_RESULT
        assert widget._result_text == recognized
        assert len(requests) == 1
        assert requests[0]["messages"][0]["content"] == DEFAULT_OCR_PROMPT
        assert requests[0]["messages"][1]["content"][-1]["text"] == IMAGE_OCR_REQUEST_TEXT
    finally:
        widget.close()
        deadline = time.monotonic() + 5
        while widget._worker is not None and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
