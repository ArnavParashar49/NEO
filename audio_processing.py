"""Adaptive noise gate — attenuates background while keeping speech for the Live API."""
from __future__ import annotations

import time

import numpy as np

_STRENGTH = {
    "soft":   {"mult": 4.5, "atten": 0.12, "hold": 0.28},
    "normal": {"mult": 6.0, "atten": 0.02, "hold": 0.38},
    "strong": {"mult": 8.5, "atten": 0.0,  "hold": 0.45},
}


class NoiseGate:
    """Learn local noise floor and pass only speech-like chunks to Gemini."""

    def __init__(self, strength: str = "normal"):
        cfg = _STRENGTH.get(strength, _STRENGTH["normal"])
        self._mult = cfg["mult"]
        self._atten = cfg["atten"]
        self._hold_sec = cfg["hold"]
        self._floor = 380.0
        self._open = False
        self._hold_until = 0.0

    def process(self, pcm: bytes) -> bytes:
        samples = np.frombuffer(pcm, dtype=np.int16)
        if samples.size == 0:
            return pcm

        peak = float(np.max(np.abs(samples.astype(np.int32))))
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
        level = max(peak, rms)

        if level < self._floor * 1.6:
            self._floor = max(100.0, self._floor * 0.94 + level * 0.06)

        threshold = self._floor * self._mult
        now = time.monotonic()

        if level >= threshold:
            self._open = True
            self._hold_until = now + self._hold_sec
        elif now > self._hold_until:
            self._open = False

        if self._open:
            return pcm

        if self._atten <= 0.0:
            return np.zeros(samples.shape, dtype=np.int16).tobytes()

        return (samples.astype(np.float32) * self._atten).astype(np.int16).tobytes()
