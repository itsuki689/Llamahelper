"""生成应用图标：深色圆角方块 + 居中白圆点（Apple 风格）。

离屏渲染，输出：
  assets/icon.png  — 256x256，供窗口图标使用
  assets/icon.ico  — 多尺寸（16-256），供 PyInstaller --icon 使用

用法: python tools/make_icon.py
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QLinearGradient,
                           QPainter, QRadialGradient)

SIZES = [16, 24, 32, 48, 64, 128, 256]
ASSETS = Path(__file__).resolve().parent.parent / "assets"


def render_icon(size: int) -> QImage:
    """渲染指定尺寸的图标（圆角外透明）。"""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    margin = size * 0.02
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    radius = size * 0.225  # iOS 圆角比例

    # 背景：深色竖向渐变（上浅下深，Apple 深色质感）
    bg = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    bg.setColorAt(0.0, QColor("#3A3A3E"))
    bg.setColorAt(1.0, QColor("#141416"))
    p.setBrush(bg)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(rect, radius, radius)

    # 白圆点：径向微渐变 + 柔和投影
    d = size * 0.34
    center = QPointF(size / 2, size / 2)
    shadow_r = d * 0.62
    shadow = QRadialGradient(QPointF(center.x(), center.y() + size * 0.012),
                             shadow_r)
    shadow.setColorAt(0.0, QColor(0, 0, 0, 90))
    shadow.setColorAt(1.0, QColor(0, 0, 0, 0))
    p.setBrush(shadow)
    p.drawEllipse(center, shadow_r, shadow_r)

    dot_rect = QRectF(center.x() - d / 2, center.y() - d / 2, d, d)
    dot = QRadialGradient(QPointF(center.x() - d * 0.18,
                                  center.y() - d * 0.22), d * 0.75)
    dot.setColorAt(0.0, QColor("#FFFFFF"))
    dot.setColorAt(1.0, QColor("#E8E8ED"))
    p.setBrush(dot)
    p.drawEllipse(dot_rect)

    p.end()
    return img


def write_ico(pngs: list[bytes], sizes: list[int], path: Path) -> None:
    """把 PNG 数据打包成 Windows .ico（Vista+ 支持 PNG 压缩条目）。"""
    count = len(pngs)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    entries = []
    blob = b""
    for data, size in zip(pngs, sizes):
        w = size if size < 256 else 0  # 256 在 ICO 里记为 0
        entries.append(struct.pack(
            "<BBBBHHII", w, w, 0, 0, 1, 32, len(data), offset))
        blob += data
        offset += len(data)
    path.write_bytes(header + b"".join(entries) + blob)


def main() -> None:
    app = QGuiApplication([])  # noqa: F841
    ASSETS.mkdir(exist_ok=True)
    pngs: list[bytes] = []
    for size in SIZES:
        img = render_icon(size)
        out = ASSETS / f"icon_{size}.png"
        img.save(str(out))
        pngs.append(out.read_bytes())
        if size == 256:
            img.save(str(ASSETS / "icon.png"))
    write_ico(pngs, SIZES, ASSETS / "icon.ico")
    # 清理中间尺寸，只保留 icon.png / icon.ico
    for size in SIZES:
        (ASSETS / f"icon_{size}.png").unlink(missing_ok=True)
    print(f"icons written to {ASSETS}")


if __name__ == "__main__":
    main()
