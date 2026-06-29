"""
orbcore — pure-numpy renderer for a modern, fluid voice orb.

No GUI dependency. Produces an (H, W, 4) uint8 RGBA array per frame designed to
be alpha-composited over anything (a transparent desktop window, a canvas).

Four states cross-fade smoothly: idle · listening · thinking · speaking.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

K = 5  # number of orbiting colour blobs


@dataclass(frozen=True)
class StatePreset:
    name: str
    colors: tuple
    breathe_speed: float
    breathe_depth: float
    swirl_speed: float
    energy: float


PRESETS: dict[str, StatePreset] = {
    "idle": StatePreset(
        "idle",
        ((90, 150, 255), (150, 110, 255), (80, 215, 255),
         (120, 130, 255), (190, 140, 255)),
        breathe_speed=1.1, breathe_depth=0.05, swirl_speed=0.5, energy=0.5),
    "listening": StatePreset(
        "listening",
        ((60, 230, 210), (70, 200, 255), (150, 255, 190),
         (60, 255, 170), (110, 220, 255)),
        breathe_speed=2.2, breathe_depth=0.08, swirl_speed=1.0, energy=0.9),
    "thinking": StatePreset(
        "thinking",
        ((190, 90, 255), (130, 90, 255), (255, 95, 210),
         (100, 120, 255), (210, 110, 255)),
        breathe_speed=3.4, breathe_depth=0.05, swirl_speed=2.6, energy=1.3),
    "speaking": StatePreset(
        "speaking",
        ((255, 120, 95), (255, 85, 160), (255, 175, 90),
         (255, 110, 130), (255, 150, 110)),
        breathe_speed=1.2, breathe_depth=0.07, swirl_speed=0.40, energy=0.60),
}


def _lerp(a, b, t):
    return a + (b - a) * t


@dataclass
class Palette:
    colors: np.ndarray
    breathe_speed: float
    breathe_depth: float
    swirl_speed: float
    energy: float

    @classmethod
    def from_preset(cls, p: StatePreset) -> "Palette":
        return cls(np.array(p.colors, np.float32) / 255.0,
                   p.breathe_speed, p.breathe_depth, p.swirl_speed, p.energy)

    def approach(self, p: StatePreset, t: float) -> None:
        tgt = np.array(p.colors, np.float32) / 255.0
        self.colors += (tgt - self.colors) * t
        self.breathe_speed = _lerp(self.breathe_speed, p.breathe_speed, t)
        self.breathe_depth = _lerp(self.breathe_depth, p.breathe_depth, t)
        self.swirl_speed = _lerp(self.swirl_speed, p.swirl_speed, t)
        self.energy = _lerp(self.energy, p.energy, t)


def _blur_axis(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    n = a.shape[axis]
    c = np.cumsum(a, axis=axis)
    zshape = list(a.shape)
    zshape[axis] = 1
    c = np.concatenate([np.zeros(zshape, a.dtype), c], axis=axis)
    idx = np.arange(n)
    hi = np.minimum(idx + r + 1, n)
    lo = np.maximum(idx - r, 0)
    take_hi = np.take(c, hi, axis=axis)
    take_lo = np.take(c, lo, axis=axis)
    shape = [1] * a.ndim
    shape[axis] = n
    count = (hi - lo).reshape(shape)
    return (take_hi - take_lo) / count


def box_blur(a: np.ndarray, r: int, passes: int = 2) -> np.ndarray:
    if r < 1:
        return a
    for _ in range(passes):
        a = _blur_axis(a, r, 0)
        a = _blur_axis(a, r, 1)
    return a


class OrbField:
    def __init__(self, size: int = 360, supersample: float = 0.75) -> None:
        self.size = size
        self.palette = Palette.from_preset(PRESETS["idle"])
        self.state = "idle"
        self.t = 0.0
        self.audio_level = 0.0

        self._w = max(8, int(size * supersample))
        self._h = self._w

        lin = (np.arange(self._w, dtype=np.float32) - (self._w - 1) / 2.0)
        lin /= (self._w / 2.0)
        self._ny, self._nx = np.meshgrid(lin, lin, indexing="ij")
        self._dist = np.sqrt(self._nx ** 2 + self._ny ** 2)

        self._phase = np.array([k * (2 * math.pi / K) for k in range(K)], np.float32)
        self._spin = np.array([0.55, -0.72, 0.63, -0.48, 0.80], np.float32)[:K]

        e = np.clip((self._dist - 0.82) / 0.18, 0.0, 1.0)
        self._edge = (1.0 - (e * e * (3.0 - 2.0 * e))).astype(np.float32)

    def set_state(self, state: str) -> None:
        if state in PRESETS:
            self.state = state

    def set_level(self, level: float) -> None:
        self.audio_level = float(max(0.0, min(1.0, level)))

    def update(self, dt: float, external_level: float | None = None) -> None:
        self.t += dt
        blend = 1.5 if self.state == "speaking" else 3.0
        self.palette.approach(PRESETS[self.state], min(1.0, dt * blend))
        target = external_level if external_level is not None else self._synthetic_level()
        smooth = 2.5 if self.state == "speaking" else 8.0
        self.audio_level += (target - self.audio_level) * min(1.0, dt * smooth)

    def _synthetic_level(self) -> float:
        s, t = self.state, self.t
        if s == "idle":
            return 0.18 + 0.10 * (0.5 + 0.5 * math.sin(t * 1.1))
        if s == "listening":
            return 0.40 + 0.28 * (0.5 + 0.5 * math.sin(t * 4.2))
        if s == "thinking":
            n = 0.5 + 0.5 * math.sin(t * 9.0) * math.sin(t * 3.7)
            return 0.45 + 0.35 * abs(n)
        if s == "speaking":
            env = 0.5 * abs(math.sin(t * 1.3)) + 0.5 * abs(math.sin(t * 2.1 + 0.8))
            return 0.34 + 0.32 * env
        return 0.2

    def render(self) -> np.ndarray:
        p = self.palette
        t = self.t
        audio = self.audio_level
        swirl = p.swirl_speed * (1.0 + (0.35 if self.state == "speaking" else 0.7) * audio)

        breathe = 1.0 + p.breathe_depth * math.sin(t * p.breathe_speed)
        radius = 0.58 * breathe * (1.0 + (0.10 if self.state == "speaking" else 0.20) * audio)

        ux = self._nx / radius
        uy = self._ny / radius
        ru = self._dist / radius

        wamp = (0.16 + 0.12 * audio) * (0.5 + 0.5 * p.energy)
        if self.state == "speaking":
            wamp *= 0.72
        fx = ux + wamp * np.sin(2.3 * uy + t * 0.7 * swirl) \
            + 0.5 * wamp * np.sin(4.1 * uy - t * 0.5 * swirl + 1.7)
        fy = uy + wamp * np.sin(2.3 * ux - t * 0.6 * swirl) \
            + 0.5 * wamp * np.sin(4.1 * ux + t * 0.55 * swirl + 0.6)

        colsum = np.zeros((self._h, self._w, 3), np.float32)
        wsum = np.zeros((self._h, self._w), np.float32)
        raw = np.zeros((self._h, self._w), np.float32)
        for k in range(K):
            ang = self._phase[k] + t * self._spin[k] * swirl
            blob_pulse = 0.10 * audio * math.sin(t * 3.0 + k)
            if self.state == "speaking":
                blob_pulse = 0.06 * audio * math.sin(t * 1.8 + k)
            orad = 0.40 + 0.12 * math.sin(t * 0.5 * swirl + k * 1.7) + blob_pulse
            bx = orad * math.cos(ang)
            by = orad * math.sin(ang)
            sigma = 0.44 + 0.10 * math.sin(t * 0.6 + k * 0.9)
            d2 = (fx - bx) ** 2 + (fy - by) ** 2
            wgt = np.exp(-d2 / (2.0 * sigma * sigma))
            sharp = wgt ** 1.7
            colsum += p.colors[k][None, None, :] * sharp[..., None]
            wsum += sharp
            raw += wgt

        col = colsum / (wsum[..., None] + 1e-4)
        gray = col.mean(axis=2, keepdims=True)
        col = np.clip(gray + (col - gray) * 1.45, 0.0, 1.0)
        dens = np.clip(raw * 0.6, 0.0, 1.0)

        body = 1.0 - np.clip((ru - 0.74) / 0.30, 0.0, 1.0)
        body = body * body * (3.0 - 2.0 * body)
        depth = 1.0 - 0.40 * np.clip((ru - 0.30) / 0.70, 0.0, 1.0) ** 2
        rim = np.clip((ru - 0.60) / 0.36, 0.0, 1.0)
        rim = rim * (1.0 - np.clip((ru - 0.97) / 0.16, 0.0, 1.0))
        rim = rim ** 1.6

        lum = body * depth * (0.55 + 0.7 * dens) * (0.9 + 0.35 * audio)
        color = col * lum[..., None]
        color += col * (rim * 0.5 * (0.5 + 0.7 * dens))[..., None]
        spec = np.exp(-(((ux + 0.34) ** 2 + (uy + 0.34) ** 2) / 0.07))
        color += (body * spec)[..., None] * 0.30

        alpha = np.clip(body * (0.6 + 0.4 * dens) + rim * 0.4, 0.0, 1.0)

        r_blur = max(1, int(self._w * 0.045))
        bright = np.clip(color - 0.45, 0.0, None)
        bloom = box_blur(bright, r_blur, passes=2) * 1.4
        color = np.clip(color + bloom, 0.0, 1.0)

        halo = box_blur(alpha, r_blur, passes=2)
        alpha = np.clip(alpha + halo * 0.5, 0.0, 1.0) * self._edge

        rgba = np.dstack([np.clip(color, 0, 1) * 255.0, alpha * 255.0]).astype(np.uint8)
        return np.ascontiguousarray(rgba)

    @property
    def render_size(self) -> int:
        return self._w
