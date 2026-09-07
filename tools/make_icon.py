"""生成应用图标：黑色圆形 + 居中白色竖向圆角矩形洞。

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
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QPainter, QPen,
                           QRadialGradient)

SIZES = [16, 24, 32, 48, 64, 128, 256]
ASSETS = Path(__file__).resolve().parent.parent / "assets"


def render_icon(size: int) -> QImage:
    """渲染指定尺寸的图标（圆形外透明）。"""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    margin = max(1.0, size * 0.055)
    circle = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    center = QPointF(size / 2, size / 2)

    # 黑色圆形主体：极轻的中心提亮保证大尺寸下仍有层次。
    black = QRadialGradient(
        QPointF(center.x() - size * 0.08, center.y() - size * 0.10),
        size * 0.58,
    )
    black.setColorAt(0.0, QColor("#18181B"))
    black.setColorAt(1.0, QColor("#050506"))
    p.setBrush(black)
    edge = QPen(QColor(255, 255, 255, 92), max(0.7, size * 0.006))
    p.setPen(edge)
    p.drawEllipse(circle)

    # 中央白色竖向圆角矩形洞；各尺寸分别渲染，确保 16px 下仍然清晰。
    hole_width = max(2.5, size * 0.18)
    hole_height = max(7.0, size * 0.46)
    hole = QRectF(
        center.x() - hole_width / 2,
        center.y() - hole_height / 2,
        hole_width,
        hole_height,
    )
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#F7F7FA"))
    p.drawRoundedRect(hole, hole_width / 2, hole_width / 2)

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
