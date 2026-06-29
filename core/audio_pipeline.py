"""Audio pipeline — recording, playback, noise gate, wake detection.

Extracted from the 2200-line ``main.py`` to make audio handling independently
testable. Integrates with the session state machine through callbacks.

Usage::

    pipeline = AudioPipeline(session_mgr, ui)
    config = pipeline.default_audio_devices()
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time

import numpy as np
import sounddevice as sd

from audio_processing import NoiseGate
from core.session_manager import (
    CHUNK_SIZE,
    RECEIVE_SAMPLE_RATE,
    SEND_SAMPLE_RATE,
    MicPhase,
)
from wake_listener import WakeListener

logger = logging.getLogger(__name__)

VIS_BANDS = 64


def extract_live_audio(response) -> bytes:
    """Extract audio parts without touching the SDK's warning-prone data property."""
    server_content = getattr(response, "server_content", None)
    model_turn = getattr(server_content, "model_turn", None)
    chunks: list[bytes] = []
    for part in getattr(model_turn, "parts", None) or ():
        inline = getattr(part, "inline_data", None)
        data = getattr(inline, "data", None)
        mime_type = (getattr(inline, "mime_type", "") or "").lower()
        if data and (not mime_type or mime_type.startswith("audio/")):
            chunks.append(data)
    return b"".join(chunks)


# ═══════════════════════════════════════════════════════════════════════════
# PCM helpers (pure functions, easy to test)
# ═══════════════════════════════════════════════════════════════════════════


def pcm_to_bands(data: bytes, n_bands: int = VIS_BANDS) -> list[float]:
    """Map PCM16 mono chunk to normalized frequency bands for the HUD."""
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return [0.0] * n_bands
    samples = samples / 32768.0
    n_fft = 512
    if samples.size < n_fft:
        padded = np.zeros(n_fft, dtype=np.float32)
        padded[: samples.size] = samples
    else:
        padded = samples[:n_fft]
    window = np.hanning(n_fft).astype(np.float32)
    spectrum = np.abs(np.fft.rfft(padded * window))
    spec_len = max(1, len(spectrum) - 1)
    bands: list[float] = []
    for i in range(n_bands):
        lo = int((i / n_bands) ** 1.4 * spec_len) + 1
        hi = int(((i + 1) / n_bands) ** 1.4 * spec_len) + 1
        hi = min(max(hi, lo + 1), len(spectrum))
        val = float(np.mean(spectrum[lo:hi]))
        bands.append(min(1.0, val / 4.0))
    return bands


def resample_pcm16(data: bytes, from_rate: int, to_rate: int) -> bytes:
    if from_rate == to_rate or not data:
        return data
    samples = np.frombuffer(data, dtype=np.int16)
    if samples.size == 0:
        return data
    new_len = max(1, int(round(samples.size * to_rate / from_rate)))
    x_old = np.arange(samples.size, dtype=np.float32)
    x_new = np.linspace(0, samples.size - 1, new_len, dtype=np.float32)
    out = np.interp(x_new, x_old, samples.astype(np.float32))
    return out.astype(np.int16).tobytes()


def default_audio_devices() -> tuple[int | None, int | None]:
    """Return usable input/output device indexes, falling back when needed."""
    try:
        inp, out = sd.default.device
        candidate_in = int(inp) if inp is not None and int(inp) >= 0 else None
        candidate_out = int(out) if out is not None and int(out) >= 0 else None
        in_dev = candidate_in if _usable_input_device(candidate_in) else _fallback_input_device()
        out_dev = candidate_out if _usable_output_device(candidate_out) else _fallback_output_device()
        return in_dev, out_dev
    except Exception:
        return _fallback_input_device(), _fallback_output_device()


def _fallback_input_device() -> int | None:
    try:
        devices = sd.query_devices()
        candidates: list[tuple[int, int]] = []
        for i, device in enumerate(devices):
            if int(device.get("max_input_channels", 0)) <= 0 or not _usable_input_device(i):
                continue
            name = str(device.get("name", "")).casefold()
            score = 100 if "microphone" in name or re.search(r"\bmic\b", name) else 0
            if "stereo mix" in name or "line in" in name:
                score -= 50
            candidates.append((score, i))
        if candidates:
            return max(candidates)[1]
    except Exception:
        pass
    return None


def _fallback_output_device() -> int | None:
    try:
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if int(d.get("max_output_channels", 0)) > 0 and _usable_output_device(i):
                return i
    except Exception:
        pass
    return None


def _usable_input_device(index: int | None) -> bool:
    if index is None:
        return False
    try:
        device = sd.query_devices(index)
        if int(device.get("max_input_channels", 0)) <= 0:
            return False
        sd.check_input_settings(
            device=index, samplerate=SEND_SAMPLE_RATE, channels=1, dtype="int16"
        )
        return True
    except Exception:
        return False


def _usable_output_device(index: int | None) -> bool:
    if index is None:
        return False
    try:
        device = sd.query_devices(index)
        if int(device.get("max_output_channels", 0)) <= 0:
            return False
        sd.check_output_settings(
            device=index, samplerate=RECEIVE_SAMPLE_RATE, channels=1, dtype="int16"
        )
        return True
    except Exception:
        return False


def open_playback_stream() -> tuple[sd.RawOutputStream, int]:
    """Open an output stream, trying common hardware sample rates."""
    sd.stop()
    time.sleep(0.15)
    _, out_dev = default_audio_devices()
    errors: list[str] = []
    for rate in (RECEIVE_SAMPLE_RATE, 48000, 44100, 16000):
        for dev in (out_dev, None):
            try:
                kwargs: dict = {
                    "samplerate": rate,
                    "channels": 1,
                    "dtype": "int16",
                    "blocksize": CHUNK_SIZE,
                    "latency": "high",
                }
                if dev is not None:
                    kwargs["device"] = dev
                sd.check_output_settings(
                    device=kwargs.get("device"),
                    samplerate=rate,
                    channels=1,
                    dtype="int16",
                )
                stream = sd.RawOutputStream(**kwargs)
                stream.start()
                return stream, rate
            except Exception as exc:
                errors.append(f"{rate}Hz dev={dev}: {exc}")
    raise sd.PortAudioError("Could not open speaker. Tried: " + "; ".join(errors[:3]))


# ═══════════════════════════════════════════════════════════════════════════
# AudioPipeline
# ═══════════════════════════════════════════════════════════════════════════


class AudioPipeline:
    """Manages microphone input, speaker output, wake detection, and noise gate.

    Designed to work with a ``SessionManager`` that owns the state machine.
    """

    def __init__(
        self,
        session_mgr,
        ui,
        *,
        noise_gate_strength: str = "normal",
        noise_gate_enabled: bool = True,
        clap_sensitivity: str = "normal",
        smart_mode: bool = True,
    ) -> None:
        self._mgr = session_mgr
        self.ui = ui
        self._smart_mode = smart_mode

        self._noise_gate = (
            NoiseGate(noise_gate_strength) if noise_gate_enabled else None
        )
        self._wake_listener: WakeListener | None = None
        if smart_mode:
            self._wake_listener = WakeListener(
                SEND_SAMPLE_RATE, clap_sensitivity=clap_sensitivity,
            )
            if self._wake_listener:
                self._wake_listener.ensure_loaded()

        self._wake_block_until = 0.0
        self._wake_listen_blocked_until = 0.0
        self._is_speaking = False
        self._speaking_lock = threading.Lock()
        self._speech_exclusive = threading.Lock()
        self._offline_tts_active = False
        self._suppress_live_audio_until = 0.0
        self._out_buf_lock = threading.Lock()
        self._live_out_buf: list[str] = []
        self._live_in_buf: list[str] = []
        self._followup_voice_frames = 0
        self._followup_voice_engaged = False
        self._voice_activity_frames = 0
        self._ack_spoken_this_turn = False
        self._neo_streaming = False
        self._wake_lock = threading.Lock()
        self._greeting_in_flight = False

        self.on_wake_trigger = None
        self.on_input_transcription = None
        self.on_turn_complete = None

    # ── Speaking guard ──────────────────────────────────────────────────

    def is_speaking(self) -> bool:
        with self._speaking_lock:
            return self._is_speaking

    def set_speaking(self, speaking: bool, *, block_wake_after: bool = True) -> None:
        with self._speaking_lock:
            self._is_speaking = speaking
        self.ui.set_speaking_active(speaking)

    def try_begin_offline_speech(self) -> bool:
        if not self._speech_exclusive.acquire(blocking=False):
            return False
        self._offline_tts_active = True
        self._suppress_live_audio_until = time.time() + 90.0
        self._drain_incoming_audio()
        with self._speaking_lock:
            self._is_speaking = True
        return True

    def end_offline_speech(self) -> None:
        with self._speaking_lock:
            self._is_speaking = False
        self._offline_tts_active = False
        self._suppress_live_audio_until = time.time() + 0.5
        try:
            self._speech_exclusive.release()
        except RuntimeError:
            pass

    def enqueue_pcm(self, pcm: bytes) -> None:
        if not self._mgr.out_queue:
            return
        try:
            self._mgr.out_queue.put_nowait({"data": pcm, "mime_type": "audio/pcm"})
        except asyncio.QueueFull:
            pass

    def _drain_incoming_audio(self):
        q = self._mgr.audio_in_queue
        if not q:
            return
        while True:
            try:
                q.get_nowait()
            except Exception:
                break

    # ── Async coroutines ────────────────────────────────────────────────

    async def send_realtime(self) -> None:
        while True:
            msg = await self._mgr.out_queue.get()
            if not self._mgr.session:
                continue
            try:
                await self._mgr.session.send_realtime_input(media=msg)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Mic send stopped: %s", e)
                return

    async def listen_audio(self) -> None:
        """Read PCM16 from the microphone and send to the session."""
        loop = asyncio.get_event_loop()
        in_dev, _ = default_audio_devices()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                neo_speaking = self._is_speaking
            pcm = indata.tobytes()
            phase = self._mgr.get_phase()

            # Wake-word detection
            if (
                self._wake_listener
                and phase in (MicPhase.STANDBY, MicPhase.FOLLOWUP)
                and not self.ui.siri_blocks_wake()
                and not self.ui.manual_mute
                and not self._mgr._processing
                and not self._offline_tts_active
                and not self._speech_exclusive.locked()
                and not neo_speaking
                and time.time() >= self._wake_listen_blocked_until
            ):
                trigger = self._wake_listener.feed(pcm)
                if trigger and self.on_wake_trigger:
                    self.ui.siri_wake()
                    loop.call_soon_threadsafe(self.on_wake_trigger, trigger)

            # Mic live -> send audio
            if self._mgr.mic_live and not neo_speaking:
                if self._noise_gate:
                    pcm = self._noise_gate.process(pcm)
                self._extend_listen_on_voice(pcm)
                if not self._followup_voice_gate(pcm):
                    return
                self.ui.push_audio_levels(pcm_to_bands(pcm))
                loop.call_soon_threadsafe(self.enqueue_pcm, pcm)

        kwargs: dict = {
            "samplerate": SEND_SAMPLE_RATE,
            "channels": 1,
            "dtype": "int16",
            "blocksize": CHUNK_SIZE,
            "latency": "high",
        }
        if in_dev is not None:
            kwargs["device"] = in_dev

        stream = None
        try:
            stream = sd.InputStream(**kwargs, callback=callback)
            with stream:
                logger.info("Mic stream open (device=%s)", in_dev or "default")
                while True:
                    await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Mic disabled: %s (Text-only mode)", e)
            while True:
                await asyncio.sleep(3600)
        finally:
            sd.stop()

    async def receive_audio(self) -> None:
        """Receive audio + server content from the Gemini Live session."""
        try:
            while True:
                async for response in self._mgr.session.receive():
                    audio_data = extract_live_audio(response)
                    if audio_data:
                        if time.time() < self._suppress_live_audio_until:
                            continue
                        if self._mgr._turn_done_event and self._mgr._turn_done_event.is_set():
                            self._mgr._turn_done_event.clear()
                        self._mgr.audio_in_queue.put_nowait(audio_data)

                    if response.server_content:
                        sc = response.server_content
                        phase = self._mgr.get_phase()

                        if (
                            sc.output_transcription
                            and sc.output_transcription.text
                            and phase != MicPhase.WAKE_SPEAKING
                        ):
                            from core.session_manager import _clean_transcript, _merge_transcript
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt:
                                with self._out_buf_lock:
                                    self._live_out_buf = _merge_transcript(
                                        self._live_out_buf, txt,
                                    )
                                    live = " ".join(self._live_out_buf).strip()
                                if live and not self._is_ack_only(live):
                                    self.ui.stream_neo(live)
                                    self._neo_streaming = True

                        if sc.input_transcription and sc.input_transcription.text:
                            from core.session_manager import _clean_transcript
                            txt = _clean_transcript(sc.input_transcription.text)
                            finished = bool(
                                getattr(sc.input_transcription, "finished", False)
                            )
                            if txt:
                                with self._out_buf_lock:
                                    self._live_in_buf.append(txt)
                                if self.on_input_transcription:
                                    self.on_input_transcription(txt, finished=finished)

                        if sc.turn_complete:
                            if self._mgr._turn_done_event:
                                self._mgr._turn_done_event.set()
                            self._mgr._turn_finalize_pending = True

                    if response.tool_call:
                        asyncio.create_task(
                            self._on_tool_call(response.tool_call),
                            name="NEO-tool-call",
                        )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err = str(e).lower()
            if any(tok in err for tok in ("1006", "1008", "keepalive", "closed", "abnormal closure")):
                logger.info("Live session dropped \u2014 reconnecting.")
            else:
                logger.exception("Recv error: %s", e)
            self._mgr.reset_live_session_state()
            self.set_speaking(False, block_wake_after=False)
            return

    async def _on_tool_call(self, tool_call) -> None:
        """Dispatch tool calls to the tool runner."""
        from core import tool_runner as _tr
        await _tr.handle_tool_calls(tool_call, session_mgr=self._mgr, audio=self)

    async def idle_mic_watch(self) -> None:
        """Close the mic after silence or timeout."""
        while True:
            await asyncio.sleep(0.25)
            if not self._smart_mode or self.ui.manual_mute:
                continue
            phase = self._mgr.get_phase()
            if phase not in (MicPhase.USER_SPEAKING, MicPhase.FOLLOWUP):
                continue
            with self._speaking_lock:
                if self._is_speaking:
                    continue
            if self._mgr._processing or self._neo_streaming or self._mgr._turn_finalize_pending:
                continue
            if self._mgr.audio_in_queue and not self._mgr.audio_in_queue.empty():
                continue
            now = time.time()
            orb_idle = self._mgr._orb_hide_at > 0 and now >= self._mgr._orb_hide_at
            if orb_idle:
                self._mgr.go_standby()
            elif phase == MicPhase.FOLLOWUP and now >= self._mgr._followup_deadline:
                self._mgr.go_standby()
            elif (
                phase == MicPhase.USER_SPEAKING
                and self._mgr._listen_deadline > 0
                and now >= self._mgr._listen_deadline
            ):
                self._mgr.go_standby()

    async def play_audio(self) -> None:
        """Play received audio chunks to the speaker."""
        stream: sd.RawOutputStream | None = None
        play_rate = RECEIVE_SAMPLE_RATE
        playback_ok = True
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self._mgr.audio_in_queue.get(), timeout=0.1,
                    )
                except asyncio.TimeoutError:
                    if time.time() < self._suppress_live_audio_until:
                        continue
                    if (
                        self._mgr._turn_done_event
                        and self._mgr._turn_done_event.is_set()
                        and self._mgr.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._mgr._turn_done_event.clear()
                        if self._mgr._turn_finalize_pending:
                            await asyncio.sleep(0.55)
                            if self._mgr.audio_in_queue.empty():
                                self._finalize_turn()
                        self._try_begin_followup()
                    continue
                if stream is None:
                    if not playback_ok:
                        continue
                    try:
                        stream, play_rate = await asyncio.to_thread(open_playback_stream)
                    except Exception as e:
                        playback_ok = False
                        logger.warning("Play open failed: %s", e)
                        continue
                if time.time() < self._suppress_live_audio_until:
                    continue
                phase = self._mgr.get_phase()
                if phase in (MicPhase.USER_SPEAKING, MicPhase.FOLLOWUP):
                    self._mark_neo_reply_started()
                self.set_speaking(True)
                if play_rate != RECEIVE_SAMPLE_RATE:
                    chunk = resample_pcm16(chunk, RECEIVE_SAMPLE_RATE, play_rate)
                self.ui.push_audio_levels(pcm_to_bands(chunk))
                try:
                    await asyncio.to_thread(stream.write, chunk)
                except Exception as e:
                    logger.warning("Play write: %s", e)
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
                    stream = None
                    sd.stop()
                    playback_ok = False
        except asyncio.CancelledError:
            raise
        finally:
            self.set_speaking(False)
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
            sd.stop()

    # ── Internal helpers ────────────────────────────────────────────────

    def _extend_listen_on_voice(self, pcm: bytes) -> None:
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(samples ** 2)))
        if rms < 45.0:
            self._voice_activity_frames = max(0, self._voice_activity_frames - 1)
        else:
            self._voice_activity_frames += 1
            if self._voice_activity_frames >= 3 and self._mgr._listen_deadline > 0:
                self._mgr._listen_deadline = time.time() + 2.0

    def _followup_voice_gate(self, pcm: bytes) -> bool:
        phase = self._mgr.get_phase()
        if phase != MicPhase.FOLLOWUP:
            return True
        if self._followup_voice_engaged:
            return True
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(samples ** 2)))
        if rms > 60.0:
            self._followup_voice_frames += 1
            if self._followup_voice_frames >= 4:
                self._followup_voice_engaged = True
        else:
            self._followup_voice_frames = max(0, self._followup_voice_frames - 1)
        return False

    def _is_ack_only(self, text: str) -> bool:
        import re
        t = re.sub(r"\s+", " ", text.strip().lower()).rstrip(".!?")
        return t in ("one moment", "just a moment", "one sec", "one second", "give me a moment")

    def _mark_neo_reply_started(self) -> None:
        self._mgr._user_spoke_this_turn = False
        self._mgr.set_phase(MicPhase.AI_RESPONDING)

    def _try_begin_followup(self) -> None:
        phase = self._mgr.get_phase()
        if phase not in (MicPhase.AI_RESPONDING, MicPhase.USER_SPEAKING) or self._mgr._processing:
            return
        with self._speaking_lock:
            if self._is_speaking:
                return
        if self._mgr.out_queue and not self._mgr.out_queue.empty():
            return
        if self._mgr.audio_in_queue and not self._mgr.audio_in_queue.empty():
            return
        self._mgr._begin_followup(open_mic_immediately=True)

    def _finalize_turn(self) -> None:
        if self.on_turn_complete:
            self.on_turn_complete()
