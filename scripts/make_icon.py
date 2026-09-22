"""Render the bundled SVG at Windows icon sizes for PyInstaller."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "assets" / "elios_colorizer.svg"
ICO = ROOT / "assets" / "elios_colorizer.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int) -> Image.Image:
    image = QImage(QSize(size, size), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    QSvgRenderer(str(SVG)).render(painter)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return Image.open(BytesIO(bytes(buffer.data()))).convert("RGBA")


def main() -> None:
    app = QGuiApplication([])
    images = [render(size) for size in SIZES]
    images[-1].save(ICO, format="ICO", sizes=[(size, size) for size in SIZES])
    app.quit()


if __name__ == "__main__":
    main()
