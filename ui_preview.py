"""Visual preview harness for the new UI components (orb, later the panel).

Run:  python ui_preview.py
Renders the components in isolation so we can screenshot + iterate on the look
without the full voice app.
"""

import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from ui_buddy import PixelBuddy


class _Bg(QWidget):
    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(13, 13, 17))


def _shot_then_quit(app, win):
    if "--shot" in sys.argv:
        path = sys.argv[sys.argv.index("--shot") + 1]
        QTimer.singleShot(700, lambda: (win.grab().save(path), app.quit()))


def main():
    app = QApplication(sys.argv)

    # play: one interactive robot — hover (happy face) + click (360 spin)
    if "--play" in sys.argv:
        win = _Bg()
        win.setWindowTitle("ARIA — hover & click me")
        win.setFixedSize(360, 360)
        lay = QVBoxLayout(win)
        lay.setContentsMargins(30, 24, 30, 24)
        buddy = PixelBuddy()
        buddy.setFixedSize(200, 220)
        buddy.state = "STANDBY"
        lay.addWidget(buddy, alignment=Qt.AlignmentFlag.AlignCenter)
        tip = QLabel("hover me  ·  click me")
        tip.setStyleSheet('color:#8aa0a0; font-family:"Menlo"; font-size:11pt;')
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(tip)
        win.move(200, 160)
        win.show()
        win.raise_()
        win.activateWindow()
        sys.exit(app.exec())

    # colors: the robot in every theme
    if "--colors" in sys.argv:
        from PyQt6.QtWidgets import QGridLayout
        from ui_buddy import THEMES
        win = _Bg()
        win.setWindowTitle("ARIA — robot colors")
        win.setFixedSize(660, 400)
        grid = QGridLayout(win)
        grid.setContentsMargins(30, 24, 30, 24)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        for i, name in enumerate(THEMES.keys()):
            r, c = divmod(i, 4)
            cell = QVBoxLayout()
            cell.setSpacing(2)
            b = PixelBuddy()
            b.setFixedSize(120, 120)
            b.set_theme(name)
            b._preview_pose = "normal"
            cell.addWidget(b, alignment=Qt.AlignmentFlag.AlignCenter)
            lab = QLabel(name)
            lab.setStyleSheet('color:#cfd6cf; font-family:"Menlo"; font-size:11pt;')
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.addWidget(lab)
            cont = QWidget()
            cont.setLayout(cell)
            grid.addWidget(cont, r, c)
        win.move(120, 100)
        win.show()
        win.raise_()
        win.activateWindow()
        _shot_then_quit(app, win)
        sys.exit(app.exec())

    # combined: buddy states (top) + the retro panel (bottom) in one window
    if "--combined" in sys.argv:
        from ui_panel import RetroPanel
        win = _Bg()
        win.setWindowTitle("ARIA — buddy + window")
        win.setFixedSize(520, 800)
        outer = QVBoxLayout(win)
        outer.setContentsMargins(24, 18, 24, 24)
        outer.setSpacing(8)

        title = QLabel("ARIA  ·  pixel buddy + cozy retro window")
        title.setStyleSheet('color:#67d493; font-family:"Menlo"; font-size:11pt;')
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(title)

        buddies = QHBoxLayout()
        buddies.setSpacing(8)
        for st, pose in [("STANDBY", "normal"), ("SPEAKING", "talk"), ("LISTENING", "normal")]:
            b = PixelBuddy()
            b.setFixedSize(120, 120)
            b.state = st
            b._preview_pose = pose
            buddies.addWidget(b)
        outer.addLayout(buddies)

        panel = RetroPanel()
        outer.addWidget(panel, stretch=1)

        win.move(120, 60)
        win.show()
        win.raise_()
        win.activateWindow()
        sys.exit(app.exec())

    # panel mode: the cozy retro full window
    if "--panel" in sys.argv:
        from ui_panel import RetroPanel
        win = _Bg()
        win.setFixedSize(420, 560)
        lay = QVBoxLayout(win)
        lay.setContentsMargins(28, 28, 28, 28)
        panel = RetroPanel()
        lay.addWidget(panel)
        win.move(160, 120)
        win.show()
        _shot_then_quit(app, win)
        sys.exit(app.exec())

    win = _Bg()
    win.setWindowTitle("ARIA UI preview")
    win.setFixedSize(880, 320)

    row = QHBoxLayout(win)
    row.setContentsMargins(40, 28, 40, 28)
    row.setSpacing(30)

    # (state, label, forced pose)
    items = [
        ("STANDBY", "Idle", "normal"),
        ("STANDBY", "Blink", "blink"),
        ("SPEAKING", "Talk", "talk"),
        ("LISTENING", "Listening", "normal"),
    ]
    for st, label, pose in items:
        col = QVBoxLayout()
        col.setSpacing(16)
        buddy = PixelBuddy()
        buddy.setFixedSize(150, 160)
        buddy.state = st
        buddy._preview_pose = pose
        col.addWidget(buddy, alignment=Qt.AlignmentFlag.AlignCenter)
        lab = QLabel(label)
        lab.setStyleSheet('color:#9a9aa6; font-family:"Helvetica Neue"; font-size:12pt;')
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(lab)
        row.addLayout(col)

    win.move(160, 160)
    win.show()

    # --shot PATH : render to a PNG after a moment, then quit (no screen-grab)
    if "--shot" in sys.argv:
        path = sys.argv[sys.argv.index("--shot") + 1]

        def _cap():
            win.grab().save(path)
            app.quit()

        QTimer.singleShot(700, _cap)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
