import sys
from PySide6.QtWidgets import QApplication
import threading
import time

def dump_windows():
    time.sleep(2)
    with open("windows_dump.txt", "w") as f:
        app = QApplication.instance()
        if not app:
            f.write("No QApplication\n")
            return
        for w in app.topLevelWidgets():
            f.write(f"Widget: {w.objectName()} | Class: {w.__class__.__name__} | Rect: {w.geometry()} | Visible: {w.isVisible()}\n")

threading.Thread(target=dump_windows, daemon=True).start()
