"""Render translation-widget states for visual regression checks."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from ui.theme import APP_QSS, FONT_STACK
from widget.settings import WidgetSettings
from widget.dockable_translate_widget import (
    MODE_PROCESSING, MODE_RESULT, RING_HOVER, RING_PROCESSING_SMALL,
    STRIPE_HOVER, TranslateWidget,
)


ROOT = Path(__file__).resolve().parent.parent
FAKE_SERVER = ROOT / "tests" / "fake_llama_server.py"
OUTPUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "screenshots"


def process_until(app: QApplication, condition, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


def save_widget(app: QApplication, widget: TranslateWidget, name: str) -> None:
    widget.show()
    app.processEvents()
    if not widget.grab().save(str(OUTPUT_DIR / name)):
        raise RuntimeError(f"Could not save {name}")


def reserve_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_server(port: int) -> None:
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("Fake llama-server did not become ready")


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    settings_dir = Path(tempfile.mkdtemp(prefix="llamahelper-widget-"))
    app = QApplication([])
    # The offscreen Qt platform does not enumerate Windows system fonts.
    # Load the application's Windows fonts explicitly for readable snapshots.
    for font_path in (
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf"),
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/msyhbd.ttc"),
    ):
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))
    app.setStyleSheet(APP_QSS.replace(FONT_STACK, '"Microsoft YaHei UI"'))

    idle = TranslateWidget(
        "http://127.0.0.1:1/v1", WidgetSettings(settings_dir / "idle.json")
    )
    save_widget(app, idle, "translation_idle.png")
    idle._animate_ring(RING_HOVER, 1)
    process_until(app, lambda: abs(idle.ring_diameter - RING_HOVER) < 0.1)
    save_widget(app, idle, "translation_hover.png")
    idle.close()

    processing = TranslateWidget(
        "http://127.0.0.1:1/v1", WidgetSettings(settings_dir / "processing.json")
    )
    processing._set_mode(MODE_PROCESSING)
    processing._set_ring_diameter(RING_PROCESSING_SMALL)
    save_widget(app, processing, "translation_processing.png")
    processing.close()

    long_result = TranslateWidget(
        "http://127.0.0.1:1/v1", WidgetSettings(settings_dir / "long.json")
    )
    long_result.move(120, 160)
    long_result._anchor_center = long_result.geometry().center()
    long_result._show_result(
        "这是一个较长的翻译结果。组件会保持白色轮廓，并在超过合理的单行长度后，"
        "沿当前锚点向下展开为圆角矩形，让内容自然换行显示。"
    )
    process_until(app, lambda: long_result.mode == MODE_RESULT
                  and long_result._content_opacity > 0.99)
    save_widget(app, long_result, "translation_long_result.png")
    long_result.close()

    docked = TranslateWidget(
        "http://127.0.0.1:1/v1", WidgetSettings(settings_dir / "docked.json")
    )
    docked.dock_to("right", animate=False)
    save_widget(app, docked, "translation_docked.png")
    docked._animate_stripe(STRIPE_HOVER, 1)
    process_until(app, lambda: abs(docked.stripe_length - STRIPE_HOVER) < 0.1)
    save_widget(app, docked, "translation_docked_hover.png")
    docked.close()

    port = reserve_port()
    server = subprocess.Popen(
        [sys.executable, str(FAKE_SERVER), "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_server(port)
        translated = TranslateWidget(
            f"http://127.0.0.1:{port}/v1",
            WidgetSettings(settings_dir / "translated.json"),
        )
        translated.move(120, 80)
        translated._anchor_center = translated.geometry().center()
        translated.translate("你好，世界")
        ok = process_until(
            app,
            lambda: translated.mode == MODE_RESULT
            and translated._content_opacity > 0.99
            and "[译] 你好，世界" in translated._result_text,
        )
        if not ok:
            raise RuntimeError("Translation result did not arrive")
        save_widget(app, translated, "translation_short_result.png")
        translated.close()
    finally:
        server.terminate()
        server.wait(timeout=5)

    print(f"Widget screenshots saved to {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
