"""Render ARIA's .app icon: the cyan robot centered on a rounded dark tile.

Usage: python scripts/_render_app_icon.py OUT.png [size]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/aria_icon.png"
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 1024

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    import ui_tray

    canvas = QPixmap(size, size)
    canvas.fill(QColor(0, 0, 0, 0))
    p = QPainter(canvas)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # rounded app tile (cozy slate-teal, matching the chat panel)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#181d1c"))
    radius = int(size * 0.225)
    inset = int(size * 0.06)
    p.drawRoundedRect(inset, inset, size - 2 * inset, size - 2 * inset, radius, radius)

    # robot sprite, centered, sized to ~62% of the tile
    sprite_px = int(size * 0.62)
    robot = ui_tray.robot_icon(sprite_px).pixmap(sprite_px, sprite_px)
    x = (size - robot.width()) // 2
    y = (size - robot.height()) // 2
    p.drawPixmap(x, y, robot)
    p.end()

    canvas.save(out)
    print(f"wrote {out} ({size}x{size})")


if __name__ == "__main__":
    main()
