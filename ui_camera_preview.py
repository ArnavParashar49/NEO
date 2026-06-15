"""Small live camera preview with optional YOLO object labels."""

from __future__ import annotations

import io
import threading
import time

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

try:
    import cv2
    _CV2 = True
except ImportError:
    _CV2 = False

try:
    import PIL.Image
    _PIL = True
except ImportError:
    _PIL = False


def _configure_capture(cap, cfg: dict) -> None:
    """Request higher resolution and low latency from the driver."""
    from actions.vision_local import apply_camera_capture_settings

    apply_camera_capture_settings(cap)
    w, h = cfg.get("width", 1280), cfg.get("height", 720)
    fps = cfg.get("fps", 30)
    aw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or w)
    ah = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or h)
    print(f"[CameraPreview] Capture {aw}x{ah} @ {cap.get(cv2.CAP_PROP_FPS) or fps} fps")


def _frame_to_pixmap(frame: np.ndarray, tw: int, th: int) -> QPixmap:
    """Resize on CPU (fast) then build pixmap — avoids heavy Qt smooth scale every tick."""
    h, w = frame.shape[:2]
    if w < 1 or h < 1:
        return QPixmap()
    scale = min(tw / w, th / h)
    if scale < 0.999:
        nw = max(1, int(w * scale))
        nh = max(1, int(h * scale))
        frame = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    fh, fw, ch = rgb.shape
    img = QImage(rgb.data, fw, fh, ch * fw, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(img)


class CameraPreviewWindow(QWidget):
    """Compact always-on-top camera feed."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ARIA Camera")
        self.setFixedSize(360, 280)
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )

        self._video = QLabel("Starting camera…")
        self._video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video.setStyleSheet(
            "background: #0a0a12; color: #8af; border-radius: 10px; font-size: 12px;"
        )
        self._status = QLabel("ARIA is looking…")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setStyleSheet("color: #ccc; font-size: 11px; padding: 4px;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        lay.addWidget(self._video, stretch=1)
        lay.addWidget(self._status)

        self.setStyleSheet(
            "background: rgba(18, 18, 28, 230); border: 1px solid rgba(120, 180, 255, 0.35);"
            " border-radius: 12px;"
        )

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def show_frame(self, frame: np.ndarray) -> None:
        if frame is None or frame.size == 0:
            return
        tw = max(1, self._video.width())
        th = max(1, self._video.height())
        pix = _frame_to_pixmap(frame, tw, th)
        if not pix.isNull():
            self._video.setPixmap(pix)

    def showEvent(self, event):
        super().showEvent(event)
        app = QApplication.instance()
        if app and app.primaryScreen():
            geo = app.primaryScreen().availableGeometry()
            self.move(geo.right() - self.width() - 16, geo.top() + 16)


class CameraPreviewManager:
    """Shared OpenCV capture + live preview window."""

    def __init__(self, embed_target_getter=None, embed_release=None):
        self._embed_target_getter = embed_target_getter
        self._embed_release = embed_release
        self._window: CameraPreviewWindow | None = None
        self._embed_label: QLabel | None = None
        self._cap = None
        self._reader: threading.Thread | None = None
        self._yolo_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest: np.ndarray | None = None
        self._display_frame: np.ndarray | None = None
        self._index = 0
        self._backend = 0
        self._yolo_live = False
        self._yolo_interval = 0.65
        self._preview_cfg: dict = {}
        self._display_timer = QTimer()
        self._display_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._display_timer.timeout.connect(self._paint_latest)

    def _pick_display_frame(self) -> np.ndarray | None:
        with self._lock:
            frame = self._display_frame if self._display_frame is not None else self._latest
            return frame.copy() if frame is not None else None

    def _paint_latest(self) -> None:
        frame = self._pick_display_frame()
        if frame is None or frame.size == 0:
            return
        if self._embed_label is not None:
            tw = max(1, self._embed_label.width())
            th = max(1, self._embed_label.height())
            pix = _frame_to_pixmap(frame, tw, th)
            if not pix.isNull():
                self._embed_label.setPixmap(pix)
        elif self._window and self._window.isVisible():
            self._window.show_frame(frame)

    def _reader_loop(self) -> None:
        """Grab frames as fast as the camera allows; UI timer picks the newest."""
        while not self._stop.is_set():
            if not self._cap or not self._cap.isOpened():
                time.sleep(0.05)
                continue
            ret, frame = self._cap.read()
            if ret and frame is not None:
                with self._lock:
                    self._latest = frame
            else:
                time.sleep(0.01)

    def _yolo_loop(self) -> None:
        from actions.vision_local import detect_objects, draw_detections, vision_config

        while not self._stop.is_set():
            time.sleep(self._yolo_interval)
            if self._stop.is_set():
                break
            if not vision_config().get("yolo"):
                continue
            with self._lock:
                raw = self._latest.copy() if self._latest is not None else None
            if raw is None:
                continue
            try:
                dets = detect_objects(raw)
                if dets:
                    labeled = draw_detections(raw, dets)
                else:
                    labeled = raw
                with self._lock:
                    self._display_frame = labeled
            except Exception:
                pass

    def _start_capture_backend(self, camera_index: int, backend: int) -> bool:
        """Open device and reader only (no QWidget). Returns True if capture is running."""
        if self._cap and self._cap.isOpened():
            return True
        self._cap = cv2.VideoCapture(camera_index, backend)
        if not self._cap.isOpened():
            print(f"[CameraPreview] Could not open camera {camera_index}")
            self._cap = None
            return False
        _configure_capture(self._cap, self._preview_cfg)
        for _ in range(3):
            self._cap.read()
        with self._lock:
            self._latest = None
            self._display_frame = None
        self._reader = threading.Thread(
            target=self._reader_loop, daemon=True, name="CameraPreviewReader",
        )
        self._reader.start()
        if self._yolo_live:
            self._yolo_thread = threading.Thread(
                target=self._yolo_loop, daemon=True, name="CameraPreviewYOLO",
            )
            self._yolo_thread.start()
        else:
            self._yolo_thread = None
        preview_fps = int(self._preview_cfg.get("preview_fps", 24))
        interval_ms = max(16, int(1000 / preview_fps))
        self._display_timer.start(interval_ms)
        print(f"[CameraPreview] Capture running ({preview_fps} fps UI)")
        return True

    def attach_display(self) -> None:
        """Wire preview to embedded panel or floating window after emerge animation."""
        if self._embed_label is not None or self._window is not None:
            return
        app = QApplication.instance()
        if app is None:
            return
        if self._embed_target_getter:
            try:
                self._embed_label = self._embed_target_getter()
            except Exception:
                self._embed_label = None
        if self._embed_label is not None:
            self._embed_label.setText("Starting camera…")
            self._embed_label.setPixmap(QPixmap())
            print("[CameraPreview] Embedded preview in panel")
            return
        self._window = CameraPreviewWindow()
        status = "Live — labels (slow)" if self._yolo_live else "ARIA is looking…"
        self._window.set_status(status)
        self._window.show()
        print("[CameraPreview] Live preview on")
        app.processEvents()

    def start(self, camera_index: int, backend: int, *, defer_display: bool = False) -> None:
        if not _CV2:
            print("[CameraPreview] OpenCV not installed")
            return

        self.hide()
        self._index = camera_index
        self._backend = backend
        self._stop.clear()

        try:
            from actions.vision_local import camera_preview_config, vision_config

            self._preview_cfg = camera_preview_config()
            vcfg = vision_config()
            self._yolo_live = bool(
                self._preview_cfg.get("yolo_live")
                and vcfg.get("enabled")
                and vcfg.get("yolo"),
            )
            self._yolo_interval = float(self._preview_cfg.get("yolo_interval_sec", 0.65))
        except Exception:
            self._preview_cfg = {"width": 1280, "height": 720, "fps": 30, "preview_fps": 24}
            self._yolo_live = False

        if not self._start_capture_backend(camera_index, backend):
            return

        app = QApplication.instance()
        if app is None:
            return

        if defer_display:
            return

        self._embed_label = None
        if self._embed_target_getter:
            try:
                self._embed_label = self._embed_target_getter()
            except Exception:
                self._embed_label = None

        if self._embed_label is not None:
            self._embed_label.setText("Starting camera…")
            self._embed_label.setPixmap(QPixmap())
            print("[CameraPreview] Embedded preview in panel")
        else:
            self._window = CameraPreviewWindow()
            status = "Live — labels (slow)" if self._yolo_live else "ARIA is looking…"
            self._window.set_status(status)
            self._window.show()
            print("[CameraPreview] Live preview on")

        app.processEvents()

    def set_status(self, text: str) -> None:
        if self._window:
            self._window.set_status(text)

    def wait_for_frame(self, timeout: float = 0.35, min_frames: int = 2) -> bool:
        """Block until the reader has delivered at least min_frames (or timeout)."""
        deadline = time.monotonic() + max(0.05, timeout)
        seen = 0
        while time.monotonic() < deadline:
            with self._lock:
                if self._latest is not None:
                    seen += 1
                    if seen >= min_frames:
                        return True
            time.sleep(0.015)
        with self._lock:
            return self._latest is not None

    def warmup(self, seconds: float = 0.6) -> None:
        if seconds > 0:
            self.wait_for_frame(timeout=seconds)

    def capture_jpeg(self, max_w: int = 1280, max_h: int = 720, quality: int = 85) -> tuple[bytes, str] | None:
        with self._lock:
            frame = self._latest.copy() if self._latest is not None else None

        if frame is None and self._cap and self._cap.isOpened():
            ret, frame = self._cap.read()
            if not ret:
                frame = None

        if frame is None:
            return None

        if _PIL:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = PIL.Image.fromarray(rgb)
            img.thumbnail((max_w, max_h), PIL.Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            return buf.getvalue(), "image/jpeg"

        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes(), "image/jpeg"

    def hide(self) -> None:
        self._display_timer.stop()
        self._stop.set()

        for th in (self._reader, self._yolo_thread):
            if th and th.is_alive():
                th.join(timeout=1.0)
        self._reader = None
        self._yolo_thread = None

        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

        if self._window:
            self._window.close()
            self._window.deleteLater()
            self._window = None

        self._embed_label = None
        if self._embed_release:
            try:
                self._embed_release()
            except Exception:
                pass

        with self._lock:
            self._latest = None
            self._display_frame = None

        app = QApplication.instance()
        if app:
            app.processEvents()
        print("[CameraPreview] Preview off")

    def is_active(self) -> bool:
        return self._cap is not None and self._cap.isOpened()
