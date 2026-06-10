"""Local wake-word ('Aria') and clap detection — no cloud, low latency."""

from __future__ import annotations



import json

import re

import sys

import threading

import time

import zipfile

from collections import deque

from pathlib import Path

from urllib.request import urlretrieve



import numpy as np



_VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"

_VOSK_MODEL_URL = (

    "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"

)

# Wake phrase is "Hey Aria". The small Vosk model mishears both words, so we
# accept tolerant variants of the "hey" prefix and the "aria" name.
_NAME_WORDS = frozenset({"aria", "area", "arya", "ariya"})
_PREFIX_WORDS = frozenset({"hey", "hay", "hi", "ok", "okay", "a"})
_WAKE_RE = re.compile(
    r"\b(hey|hay|hi|ok|okay)\s+(aria|area|arya|ariya)\b",
    re.IGNORECASE,
)





def get_base_dir() -> Path:

    if getattr(sys, "frozen", False):

        return Path(sys.executable).parent

    return Path(__file__).resolve().parent





MODELS_DIR = get_base_dir() / "models"





def _ensure_vosk_model() -> Path:

    target = MODELS_DIR / _VOSK_MODEL_NAME

    if (target / "am").exists() or (target / "graph").exists():

        return target



    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    zip_path = MODELS_DIR / f"{_VOSK_MODEL_NAME}.zip"

    print(f"[Wake] ⬇️  Downloading speech model (~40 MB)…")

    urlretrieve(_VOSK_MODEL_URL, zip_path)

    with zipfile.ZipFile(zip_path, "r") as zf:

        zf.extractall(MODELS_DIR)

    zip_path.unlink(missing_ok=True)

    print(f"[Wake] ✅ Model ready at {target}")

    return target





class ClapDetector:
    """Detects a quick double-clap from PCM16 mono chunks."""

    _PRESETS = {
        "high":   {"min_peak": 1100, "peak_mult": 3.8, "min_rms": 520, "rms_mult": 2.3},
        "normal": {"min_peak": 1500, "peak_mult": 4.5, "min_rms": 620, "rms_mult": 2.8},
        "low":    {"min_peak": 2200, "peak_mult": 5.5, "min_rms": 900, "rms_mult": 3.5},
        "strict": {
            "min_peak": 3800,
            "peak_mult": 7.0,
            "min_rms": 1400,
            "rms_mult": 5.0,
            "min_crest": 5.5,
        },
    }

    def __init__(
        self,
        min_gap: float = 0.06,
        max_gap: float = 0.65,
        cooldown: float = 6.0,
        sensitivity: str = "normal",
    ):
        self.min_gap = min_gap
        self.max_gap = max_gap
        self.cooldown = cooldown
        sens = sensitivity.lower() if sensitivity else "normal"
        self._sens = self._PRESETS.get(sens, self._PRESETS["normal"])
        self._peak_times: deque[float] = deque(maxlen=6)
        self._armed = True
        self._in_peak = False
        self._last_fire = 0.0
        self._noise_floor = 280.0
        self._cal_samples = 0



    def reset(self):
        self._peak_times.clear()
        self._in_peak = False
        self._armed = True

    def note_wake(self):
        """Called after a successful wake — suppress re-triggers for a while."""
        self._last_fire = time.time()
        self.reset()



    def _thresholds(self) -> tuple[float, float, float, float]:
        # Cap noise-floor boost so keyboard typing does not lower the clap threshold.
        nf = min(max(self._noise_floor, 180.0), 420.0)
        peak_hi = max(self._sens["min_peak"], nf * self._sens["peak_mult"])
        rms_hi = max(self._sens["min_rms"], nf * self._sens["rms_mult"])
        peak_lo = peak_hi * 0.38
        rms_lo = rms_hi * 0.38
        return peak_hi, rms_hi, peak_lo, rms_lo

    def feed(self, pcm: bytes, sample_rate: int = 16000) -> bool:
        now = time.time()
        if now - self._last_fire < self.cooldown:
            return False

        samples = np.frombuffer(pcm, dtype=np.int16)
        if samples.size == 0:
            return False

        peak = float(np.max(np.abs(samples)))
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))

        if self._cal_samples < 80:
            self._noise_floor = max(150.0, self._noise_floor * 0.94 + rms * 0.06)
            self._cal_samples += 1

        peak_hi, rms_hi, peak_lo, rms_lo = self._thresholds()
        crest = peak / max(rms, 1.0)
        min_crest = float(self._sens.get("min_crest", 4.2))
        # Claps are sharp transients; keyboard taps are softer / less peaked.
        loud = (
            peak >= peak_hi
            and rms >= rms_hi * 0.62
            and crest >= min_crest
        )
        quiet = peak < peak_lo and rms < rms_lo

        if loud:
            if self._armed:
                self._peak_times.append(now)
                self._armed = False
            self._in_peak = True
        elif self._in_peak and quiet:
            self._in_peak = False
            self._armed = True

        while self._peak_times and now - self._peak_times[0] > 1.0:
            self._peak_times.popleft()

        if len(self._peak_times) < 2:
            return False

        for i in range(len(self._peak_times) - 1):
            gap = self._peak_times[i + 1] - self._peak_times[i]
            if self.min_gap <= gap <= self.max_gap:
                self._last_fire = now
                self._peak_times.clear()
                self._armed = True
                self._in_peak = False
                print(f"[Wake] 👏 Double clap (peak={peak:.0f}, rms={rms:.0f})")
                return True
        return False





class WakeWordDetector:

    """Streaming 'Aria' spotter using Vosk partial results."""



    def __init__(self, sample_rate: int = 16000):

        self.sample_rate = sample_rate

        self._lock = threading.Lock()

        self._ready = False

        self._model = None

        self._recognizer = None

        self._cooldown = 0.0

        self._last_partial = ""

        self._loader_started = False



    def _load_model(self):

        try:

            from vosk import Model, KaldiRecognizer

        except ImportError:

            print("[Wake] ⚠️  vosk not installed — wake word disabled")

            return



        try:

            model_path = _ensure_vosk_model()

            model = Model(str(model_path))

            self._model = model

            grammar = json.dumps(["hey aria", "hey", "aria", "area", "arya", "[unk]"])

            self._recognizer = KaldiRecognizer(model, self.sample_rate, grammar)

            self._recognizer.SetWords(False)

            self._ready = True

            print("[Wake] ✅ 'Hey Aria' listener ready")

        except Exception as e:

            print(f"[Wake] ⚠️  Wake word init failed: {e}")



    def ensure_loaded(self):

        if self._ready or self._loader_started:

            return

        self._loader_started = True

        threading.Thread(target=self._load_model, daemon=True, name="WakeModelLoad").start()



    def reset(self):

        with self._lock:

            if self._model is not None:

                try:

                    from vosk import KaldiRecognizer

                    grammar = json.dumps(["hey aria", "hey", "aria", "area", "arya", "[unk]"])

                    self._recognizer = KaldiRecognizer(

                        self._model, self.sample_rate, grammar

                    )

                    self._recognizer.SetWords(False)

                except Exception:

                    pass

            self._last_partial = ""



    def _matches_wake(self, text: str) -> bool:
        t = text.strip().lower()
        if not t:
            return False
        if _WAKE_RE.search(t):
            return True
        # also catch "hey" + "aria" split across tokens in partial results
        words = t.split()
        for i in range(len(words) - 1):
            if words[i] in _PREFIX_WORDS and words[i + 1] in _NAME_WORDS:
                return True
        return False



    def feed(self, pcm: bytes) -> bool:

        self.ensure_loaded()

        if not self._ready or self._recognizer is None:

            return False



        now = time.time()

        if now < self._cooldown:

            return False



        with self._lock:

            try:

                if self._recognizer.AcceptWaveform(pcm):

                    result = json.loads(self._recognizer.Result()).get("text", "")

                    if self._matches_wake(result):
                        print(f"[Wake] 🎤 Hey Aria ({result!r})")
                        self._cooldown = now + 2.0
                        self.reset()
                        return True

                partial = json.loads(self._recognizer.PartialResult()).get("partial", "")

            except Exception:

                return False



        if partial and partial != self._last_partial:

            self._last_partial = partial

            if self._matches_wake(partial):
                print(f"[Wake] 🎤 Hey Aria ({partial!r})")
                self._cooldown = time.time() + 2.0
                self.reset()
                return True

        return False





class WakeListener:

    """Combined clap + wake-word detector with a short audio pre-buffer."""



    def __init__(self, sample_rate: int = 16000, prebuffer_seconds: float = 1.2, clap_sensitivity: str = "normal"):

        self.sample_rate = sample_rate

        self._clap = ClapDetector(sensitivity=clap_sensitivity)

        self._wake = WakeWordDetector(sample_rate)

        max_chunks = int(prebuffer_seconds * sample_rate / 1024) + 2

        self._prebuffer: deque[bytes] = deque(maxlen=max(max_chunks, 8))



    def ensure_loaded(self):

        self._wake.ensure_loaded()



    def feed(self, pcm: bytes) -> str | None:

        """Returns 'clap', 'wake', or None."""

        self._prebuffer.append(pcm)

        if self._wake.feed(pcm):
            return "wake"

        if self._clap.feed(pcm, self.sample_rate):
            return "clap"

        return None



    def drain_prebuffer(self) -> list[bytes]:

        chunks = list(self._prebuffer)

        self._prebuffer.clear()

        return chunks



    def reset(self):

        self._clap.reset()

        self._wake.reset()

    def note_activate(self):
        """After a successful wake — brief cooldown so clap/word do not re-fire."""
        self._clap.note_wake()
        self._wake.reset()
        self._wake._cooldown = time.time() + 2.0

    def note_standby(self):
        """Entering standby — reset recognizer without blocking the wake word."""
        self._clap.reset()
        self._wake.reset()

    def note_wake(self):
        self.note_activate()


