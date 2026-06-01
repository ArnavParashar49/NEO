import json
import os
import time

from PyQt6.QtCore import QUrl, QUrlQuery, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QVBoxLayout, QWidget

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    HAS_WEBENGINE = True
except ImportError:
    QWebEngineView = None  # type: ignore
    HAS_WEBENGINE = False


class ThreeJSOrbCanvas(QWidget):
    """Embeds the WebGL particle sphere (sphere_visualizer.html) via QWebEngineView."""

    def __init__(self, face_path: str = "", parent=None, compact: bool = False, transparent_bg: bool = False):
        super().__init__(parent)
        self._compact = compact
        self._transparent_bg = transparent_bg
        self._muted = False
        self._speaking = False
        self._state = "INITIALISING"
        self._pending_bands: list[float] | None = None
        self._last_band_push = 0.0
        self._loaded = False
        self._fallback = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if not HAS_WEBENGINE:
            self._attach_fallback(face_path, layout)
            return

        self.web = QWebEngineView(self)
        self.web.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.web)

        settings = self.web.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollBarEnabled, False)

        if transparent_bg:
            bg = QColor(0, 0, 0, 0)
        elif compact:
            bg = QColor(22, 22, 24, 255)
        else:
            bg = QColor(0, 0, 0, 255)
        self.web.page().setBackgroundColor(bg)

        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "assets"))
        html_path = os.path.join(base_dir, "sphere_visualizer.html")
        url = QUrl.fromLocalFile(html_path)
        q = QUrlQuery()
        if compact:
            q.addQueryItem("compact", "1")
        if transparent_bg:
            q.addQueryItem("transparent", "1")
        if not q.isEmpty():
            url.setQuery(q)
        self.web.setUrl(url)
        self.web.loadFinished.connect(self._on_loaded)

    def _attach_fallback(self, face_path: str, layout: QVBoxLayout) -> None:
        from ui_hud import QPainterOrbCanvas

        self._fallback = QPainterOrbCanvas(face_path, self)
        layout.addWidget(self._fallback)

    @property
    def uses_webgl(self) -> bool:
        return HAS_WEBENGINE and self._fallback is None

    def _run_js(self, script: str) -> None:
        if self._fallback is not None:
            return
        if HAS_WEBENGINE and hasattr(self, "web") and self._loaded:
            self.web.page().runJavaScript(script)

    def _on_loaded(self, ok: bool) -> None:
        self._loaded = ok
        if not ok:
            return
        self._run_js(f"window.setState('{self._state}');")
        self._run_js(f"window.setSpeaking({str(self._speaking).lower()});")
        self._run_js(f"window.setMuted({str(self._muted).lower()});")

    def set_audio_bands(self, bands: list[float]) -> None:
        if self._fallback is not None:
            self._fallback.set_audio_bands(bands)
            return
        self._pending_bands = bands

    def _step(self):
        if self._fallback is not None:
            self._fallback._step()
            return
        if not self._pending_bands:
            return
        now = time.time()
        if now - self._last_band_push < 0.04:
            return
        self._last_band_push = now
        js = json.dumps([round(v, 4) for v in self._pending_bands[:64]])
        self._run_js(f"window.setAudioLevels({js});")

    @property
    def muted(self):
        return self._muted

    @muted.setter
    def muted(self, value: bool):
        self._muted = value
        if self._fallback is not None:
            self._fallback.muted = value
            return
        self._run_js(f"window.setMuted({str(value).lower()});")
        if value:
            self.set_audio_bands([0.0] * 64)

    @property
    def speaking(self):
        return self._speaking

    @speaking.setter
    def speaking(self, value: bool):
        self._speaking = value
        if self._fallback is not None:
            self._fallback.speaking = value
            return
        self._run_js(f"window.setSpeaking({str(value).lower()});")

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value: str):
        self._state = value
        if self._fallback is not None:
            self._fallback.state = value
            return
        safe = str(value).replace("'", "\\'")
        self._run_js(f"window.setState('{safe}');")
