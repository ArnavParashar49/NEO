import config  # noqa: F401 — load .env before other modules

import asyncio
import datetime
import random
import re
import socket
import threading
import json
import os
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')
import time
import traceback
from enum import Enum, auto
from pathlib import Path

import numpy as np
import sounddevice as sd
from google import genai
from google.genai import types
from ui import NeoUI

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.send_email        import send_email
from actions.calendar          import calendar_control
from actions.contacts          import contact_manager
from actions.reminder          import reminder
from actions.notes             import notes_control
from actions.organizer         import organizer_control
from actions.document_tools    import document_tools
from actions.list_manager      import list_manager
from actions.computer_settings import computer_settings
from actions.system_control    import system_control
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from wake_listener             import WakeListener
from audio_processing          import NoiseGate
from core.audio_pipeline import (
    default_audio_devices as _audio_default_devices,
    open_playback_stream as _audio_open_playback_stream,
    pcm_to_bands as _audio_pcm_to_bands,
    resample_pcm16 as _audio_resample_pcm16,
    extract_live_audio as _extract_live_audio,
)
from core.session_manager import (
    _clean_transcript as _session_clean_transcript,
    _merge_transcript as _session_merge_transcript,
)
from core.tool_runner import (
    tool_progress_eta as _shared_tool_progress_eta,
)


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
from core.models import VOICE_LIVE

LIVE_MODEL          = VOICE_LIVE
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024
VIS_BANDS           = 64
FOLLOWUP_SECONDS    = 7
LISTEN_TIMEOUT_SEC  = 7
SIRI_IDLE_HIDE_SEC  = 7
WAKE_TTS_MODEL      = "gemini-2.5-flash-preview-tts"
# Prebuilt Gemini voices: Kore, Aoede (typically female), Charon, Puck, Fenrir, etc.
_DEFAULT_LIVE_VOICE = "Kore"
_SLOW_ACK_RE        = re.compile(
    r"\b(find|search|look\s*up|news|options|compare|list|recommend|"
    r"buy|price|weather|tv|television|laptop|phone|tell\s+me)\b",
    re.IGNORECASE,
)
_SHOW_IMAGES_RE     = re.compile(
    r"\b(show|see|display|view|get|dikh|dikha|dikhao|find)\b.*\b(image|images|photo|picture|pics|imaje|imaj|cover|covers|case|cases|option|options)\b|"
    r"\b(image|images|photo|picture|pics|imaje|imaj|cover|covers|case|cases)\b.*\b(show|see|here|grid|squares?|dikh)\b|"
    r"\b(show|see|find)\b.*\b(back\s+cover|phone\s+case|product|products|laptop|phone|options)\b|"
    r"not\s+see(?:ing)?\s+(?:the\s+)?\w*\s*imag|"
    r"can't\s+see\s+(?:the\s+)?\w*\s*imag|"
    r"इमेज|दिख|तस्वीर|फोटो",
    re.IGNORECASE,
)
_MIN_USER_WORDS     = 3
_LIGHT_FILLERS = (
    "Yeah, on it.",
    "Absolutely — one sec.",
    "You got it.",
    "Right, let me see.",
)
_SLOW_FILLERS = (
    "Oh yeah, I'll dig that up for you.",
    "On it — back in a sec with the goods.",
    "Sure thing — give me a moment.",
    "You bet — hunting that down now.",
)
_SYSTEM_CONTROL_RE  = re.compile(
    r"\b(volume|brightness|brighter|dimmer|dim|louder|quieter|mute|loud)\b|"
    r"increase\s+(?:the\s+)?(?:screen\s+)?brightness|"
    r"turn\s+(?:up|down)\s+(?:the\s+)?volume",
    re.IGNORECASE,
)
_SCREEN_INTENT_RE = re.compile(
    r"\b("
    r"screen|display|monitor|webcam|camera|"
    r"what(?:'s| is| am i) (?:on |looking at |showing on |holding)|"
    r"what am i holding|what(?:'s| is) (?:this|that) (?:thing|object)|"
    r"identify (?:the |this |what )|"
    r"what do you see|what can you see|can you see|"
    r"look at (?:my |the )?(?:screen|display|monitor|this|camera|webcam)|"
    r"on my screen|see (?:my |the )?screen|through the camera|"
    r"read (?:my |the )?screen|describe (?:my |the )?screen|"
    r"what(?:'s| is) (?:this|that) (?:on|in)|"
    r"help me with (?:this|what's on)"
    r")\b",
    re.IGNORECASE,
)


def _allow_screen_process(args: dict, last_user_log: str = "") -> bool:
    """Allow vision when angle=camera or the request clearly asks about screen/webcam."""
    angle = (args.get("angle") or "").lower().strip()
    if angle in ("camera", "webcam"):
        return True
    q = (args.get("text") or args.get("user_text") or "").strip()
    combined = f"{last_user_log} {q}".strip()
    return bool(_SCREEN_INTENT_RE.search(combined))


def _is_ack_only(text: str) -> bool:
    t = re.sub(r"\s+", " ", text.strip().lower()).rstrip(".!?")
    return t in ("one moment", "just a moment", "one sec", "one second", "give me a moment")


def _pcm_to_bands(data: bytes, n_bands: int = VIS_BANDS) -> list[float]:
    return _audio_pcm_to_bands(data, n_bands)


def _resample_pcm16(data: bytes, from_rate: int, to_rate: int) -> bytes:
    return _audio_resample_pcm16(data, from_rate, to_rate)


def _default_audio_devices() -> tuple[int | None, int | None]:
    return _audio_default_devices()


def _open_playback_stream() -> tuple[sd.RawOutputStream, int]:
    return _audio_open_playback_stream()


def _play_pcm_blocking(pcm: bytes, sample_rate: int = RECEIVE_SAMPLE_RATE, on_chunk=None):
    """Play PCM16 mono in the foreground (used for wake greeting)."""
    if not pcm:
        return
    stream, play_rate = _open_playback_stream()
    try:
        if play_rate != sample_rate:
            pcm = _resample_pcm16(pcm, sample_rate, play_rate)
        step = CHUNK_SIZE * 2
        for i in range(0, len(pcm), step):
            chunk = pcm[i : i + step]
            if on_chunk:
                on_chunk(chunk)
            stream.write(chunk)
    finally:
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass
        sd.stop()


def _synthesize_charon_greeting(api_key: str, text: str) -> bytes | None:
    """Fallback TTS — same Charon voice as live NEO."""
    models = (WAKE_TTS_MODEL, "gemini-2.5-pro-preview-tts")
    prompt = (
        "Speak like a witty, warm friend — natural pace, lightly playful, never stiff. "
        f"Say exactly: {text}"
    )
    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
    for model in models:
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=_live_voice_name()
                            )
                        )
                    ),
                ),
            )
            for part in response.candidates[0].content.parts:
                blob = part.inline_data
                if blob and blob.data:
                    print(f"[NEO] Wake TTS OK ({model})")
                    return blob.data
        except Exception as e:
            print(f"[NEO] Wake TTS ({model}): {e}")
    return None


def _synthesize_charon_speech(api_key: str, text: str) -> bytes | None:
    """Offline Charon TTS for filler phrases and wake lines."""
    return _synthesize_charon_greeting(api_key, text)


def _filler_from_query(query: str) -> str | None:
    q = (query or "").strip()
    if not q:
        return None
    ql = q.lower()
    if re.search(r"\bworld\s+news\b|\bglobal\s+news\b|news.*\bworld\b|"
        r"what(?:'s|\s+is)\s+going\s+on(?:\s+in|\s+around)?\s+(?:the\s+)?world",
        ql,
    ):
        return "Yeah — I'll see what's shaking around the world."
    if re.search(r"\bai\b|artificial intelligence", ql) and re.search(
        r"\bnews\b|\bgoing on\b|\blatest\b|\bfield\b|\bhappening\b", ql
    ):
        return "On it — I'll catch you up on AI news."
    if re.search(r"\bweather\b", ql):
        return "Let me check the weather — umbrella intel incoming."
    if re.search(r"\bnews\b", ql):
        topic = re.sub(
            r"\b(news|latest|tell me|what|whats|what's|the|about|on|give me)\b",
            " ",
            ql,
            flags=re.I,
        )
        topic = " ".join(topic.split())[:40].strip(" ,.-")
        if topic:
            return f"Absolutely — latest on {topic}, coming right up."
        return "Sure — I'll grab the latest headlines for you."
    return None


def _filler_from_user_text(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return random.choice(_LIGHT_FILLERS)
    matched = _filler_from_query(text)
    if matched:
        return matched
    if _SLOW_ACK_RE.search(text):
        return random.choice(_SLOW_FILLERS)
    return random.choice(_LIGHT_FILLERS)


def _session_greeting(now: datetime.datetime | None = None) -> str:
    current = now or datetime.datetime.now()
    if current.hour < 12:
        period = "morning"
    elif current.hour < 17:
        period = "afternoon"
    else:
        period = "evening"
    return (
        f"Good {period}. I'm doing well—what are we working on today? "
        "Want me to check for any genuinely important updates too?"
    )


def _tool_filler_phrase(name: str, args: dict) -> str:
    """Short spoken line while a slow tool runs."""
    if name == "web_search":
        q = (args.get("query") or "").strip()
        return _filler_from_query(q) or (
            f"On it — looking up {q[:50]}." if q else random.choice(_SLOW_FILLERS)
        )
    if name == "weather_report":
        city = (args.get("city") or "").strip()
        if city:
            return f"Checking {city}'s weather — hold the small talk with the clouds."
        return "Let me check the weather — one sec."
    if name == "agent_task":
        g = (args.get("goal") or "that")[:50]
        return f"On it — tackling {g}."
    if name == "file_processor":
        return "Processing your file — won't ghost you."
    if name == "flight_finder":
        return "Hunting flights — seatbelts optional."
    if name == "youtube_video":
        return "Pulling up that video — popcorn not included."
    if name == "screen_process":
        if (args.get("angle") or "").lower() == "camera":
            return "Opening your camera — let me see what's there."
        return "Peeking at your screen — no judgment."
    if name == "download_control":
        q = (args.get("query") or args.get("name") or "that").strip()
        return f"Downloading {q} — opening Google and the official site."
    labels = {
        "browser_control": "Browser time — one moment.",
        "send_email": "Email duty — on it.",
        "organizer_control": "Tidying files — Marie Kondo mode.",
        "document_tools": "Working on your documents — paper cuts imminent.",
    }
    return labels.get(name, random.choice(_SLOW_FILLERS))


def _tool_status_line(name: str, args: dict) -> str:
    """UI status line while a slow tool runs."""
    return _tool_filler_phrase(name, args)


def _get_api_key() -> str:
    from config import get_api_key
    return get_api_key(required=False)

_GEMINI_API_HOST = "generativelanguage.googleapis.com"

def _api_key_config_error() -> str | None:
    """Return a user-facing message when the Gemini key is missing or a placeholder."""
    key = _get_api_key().strip()
    if not key:
        return "gemini_api_key is empty — set GEMINI_API_KEY in .env (see .env.example)."
    lowered = key.lower()
    if lowered in {"your-api-key", "your_api_key", "paste-key-here"} or "your" in lowered and "key" in lowered:
        return "Replace the placeholder GEMINI_API_KEY in .env."
    return None


def _gemini_host_reachable() -> bool:
    try:
        socket.getaddrinfo(_GEMINI_API_HOST, 443, type=socket.SOCK_STREAM)
        return True
    except OSError:
        return False


def _classify_connect_error(exc: BaseException) -> str:
    """network | auth | transient | cancelled | unknown"""
    s = f"{type(exc).__name__} {exc}".lower()
    if isinstance(exc, socket.gaierror) or "nodename nor servname" in s or "name or service not known" in s:
        return "network"
    if any(
        tok in s
        for tok in (
            "invalid authentication",
            "authentication credentials",
            "api key not valid",
            "api_key_invalid",
            "permission denied",
            "unauthorized",
            "forbidden",
            "billing",
            "quota",
        )
    ):
        return "auth"
    if "1008" in s and "policy violation" in s:
        return "auth"
    if "cancelled" in s or "portaudio" in s:
        return "cancelled"
    if any(
        tok in s
        for tok in (
            "1006", "1011", "keepalive", "connectionclosed",
            "connection reset", "closed", "internal error",
        )
    ):
        return "transient"
    return "unknown"


def _connect_error_message(kind: str, exc: BaseException | None = None) -> str:
    if kind == "network":
        return "No internet — can't reach Gemini. Check Wi‑Fi or DNS, then NEO will retry."
    if kind == "auth":
        return (
            "Gemini rejected the API key. Please update it."
        )
    if kind == "transient":
        return "Live session dropped — reconnecting…"
    if exc is not None:
        return f"Connection error: {exc}"
    return "Connection error — retrying…"


def _hybrid_fast_path_enabled() -> bool:
    return bool(_load_app_config().get("hybrid_fast_path", True))


def _fast_path_args_key(tool_name: str, args: dict) -> str:
    try:
        return f"{tool_name}:{json.dumps(args or {}, sort_keys=True, default=str)}"
    except Exception:
        return f"{tool_name}:{args!r}"


def _load_app_config() -> dict:
    from config import get_config
    return get_config()


def _smart_mode_enabled() -> bool:
    return bool(_load_app_config().get("smart_mode", True))


def _noise_gate_enabled() -> bool:
    return bool(_load_app_config().get("noise_gate", True))


def _noise_gate_strength() -> str:
    s = str(_load_app_config().get("noise_gate_strength", "normal")).lower()
    return s if s in ("soft", "normal", "strong") else "normal"


def _clap_sensitivity() -> str:
    s = str(_load_app_config().get("clap_sensitivity", "strict")).lower()
    return s if s in ("low", "normal", "high", "strict") else "strict"


def _live_voice_name() -> str:
    name = str(_load_app_config().get("live_voice", _DEFAULT_LIVE_VOICE)).strip()
    return name or _DEFAULT_LIVE_VOICE


def _load_system_prompt() -> str:
    try:
        prompt = PROMPT_PATH.read_text(encoding="utf-8")
        import platform, getpass, datetime
        sys_ctx = f"\n\n[SYSTEM CONTEXT]\nOperating System: {platform.system()} {platform.release()}\nOS Account Name: {getpass.getuser()}\nCurrent Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
        # Inject memory context if available
        try:
            from core.memory_graph import format_graph_for_prompt
            sys_ctx += format_graph_for_prompt()
        except Exception:
            pass
            
        return prompt + sys_ctx
    except Exception:
        return (
            "You are NEO, personal AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

def _clean_transcript(text: str) -> str:
    return _session_clean_transcript(text)


def _merge_transcript(parts: list[str], new: str) -> list[str]:
    return _session_merge_transcript(parts, new)


def _join_transcript(parts: list[str]) -> str:
    """Join streamed text chunks without gluing adjacent words together."""
    return " ".join(part.strip() for part in parts if part and part.strip()).strip()

from hybrid.bootstrap import init_hybrid_system

_HYBRID_ORCHESTRATOR = init_hybrid_system()
_SLOW_TOOLS = _HYBRID_ORCHESTRATOR.registry.slow_tools()
# Auto-generated from registry — no hand-maintained declarations file needed
# Exclude MCP tools for the Live session to avoid 1007/1008 limit errors
CORE_TOOL_DECLARATIONS = _HYBRID_ORCHESTRATOR.registry.to_gemini_declarations(include_mcp_tools=False)


class MicPhase(Enum):
    STANDBY        = auto()   # muted — wake / clap only
    WAKE_SPEAKING  = auto()   # muted — NEO says greeting
    USER_SPEAKING  = auto()   # mic open — user talks
    AI_RESPONDING  = auto()   # muted — NEO thinking / speaking / tools
    FOLLOWUP       = auto()   # mic open — 5 s window after NEO reply


class NeoLive:

    def __init__(self, ui: NeoUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self._phase_lock    = threading.Lock()
        self.ui.on_text_command = self._on_text_command
        self.ui.on_stop_command = self._on_stop_command
        self.ui.on_force_listen = self._force_listen
        self._turn_done_event: asyncio.Event | None = None
        self._smart_mode    = _smart_mode_enabled()
        self._mic_phase     = MicPhase.STANDBY if self._smart_mode else MicPhase.USER_SPEAKING
        self._user_spoke_this_turn = False
        self._followup_deadline    = 0.0
        self._listen_deadline      = 0.0
        self._had_conversation     = False
        self._session_greeted      = False
        self._last_user_log        = ""
        self._user_line_logged     = False
        self._ack_spoken_this_turn = False
        self._followup_voice_frames  = 0
        self._followup_voice_engaged = False
        self._voice_activity_frames  = 0
        self._processing            = False
        self._last_neo_reply       = ""
        self._last_search_result    = ""
        self._last_system_command   = ""
        self._system_handled_turn   = False
        self._display_command_turn = False
        self._local_system_lock     = threading.Lock()
        self._neo_streaming        = False
        self._turn_finalize_pending = False
        self._out_buf_lock          = threading.Lock()
        self._live_out_buf: list[str] = []
        self._live_in_buf: list[str] = []
        self._wake_block_until      = 0.0
        self._wake_listen_blocked_until = 0.0
        self._orb_hide_at           = 0.0
        self._wake_lock             = threading.Lock()
        self._greeting_in_flight    = False
        self._speech_exclusive      = threading.Lock()
        self._offline_tts_active    = False
        self._suppress_live_audio_until = 0.0
        self._processing_keepalive_stop = threading.Event()
        self._processing_status_msg   = ""
        self._think_filler_stop       = threading.Event()
        self._think_filler_stop.set()
        self._tool_send_lock: asyncio.Lock | None = None
        self._fast_path_guard_lock = threading.Lock()
        self._fast_path_guard: dict | None = None
        self._fast_path_turn_used = False
        self.ui._visuals_done_cb    = self._finish_processing
        self.ui.on_force_listen     = self._force_listen
        self._noise_gate = (
            NoiseGate(_noise_gate_strength()) if _noise_gate_enabled() else None
        )
        if self._noise_gate:
            print(f"[NEO] 🔇 Noise gate on ({_noise_gate_strength()})")
        self._wake_listener = (
            WakeListener(SEND_SAMPLE_RATE, clap_sensitivity=_clap_sensitivity())
            if self._smart_mode else None
        )
        if self._wake_listener:
            self._wake_listener.ensure_loaded()
            print(f"[Wake] 👏 Clap wake enabled ({_clap_sensitivity()} sensitivity)")
        if self._smart_mode:
            self.ui.set_standby(True)
            self.ui.set_mic_live(False)
        else:
            self.ui.set_mic_live(True)

    def _get_phase(self) -> MicPhase:
        with self._phase_lock:
            return self._mic_phase

    def _set_phase(self, phase: MicPhase):
        with self._phase_lock:
            self._mic_phase = phase
        if not self._smart_mode:
            self.ui.set_mic_live(True)
            return

        mic_live = phase in (MicPhase.USER_SPEAKING, MicPhase.FOLLOWUP)
        self.ui.set_mic_live(mic_live)
        if not mic_live:
            self._drain_out_queue()

        if phase == MicPhase.STANDBY:
            self.ui.set_standby(True)
            self.ui.set_state("STANDBY")
            if self._wake_listener:
                self._wake_listener.note_standby()
        elif phase == MicPhase.USER_SPEAKING:
            self._listen_deadline = time.time() + LISTEN_TIMEOUT_SEC
            self.ui.set_standby(False)
            self.ui.siri_set_prompt("I'm listening…")
            self.ui.set_state("LISTENING")
        elif phase == MicPhase.FOLLOWUP:
            self.ui.set_standby(False)
            self.ui.siri_set_prompt("I'm listening…")
            self.ui.set_state("LISTENING")
        elif phase == MicPhase.WAKE_SPEAKING:
            self.ui.set_standby(False)
            self.ui.siri_set_prompt("I'm listening…")
            self.ui.set_state("LISTENING")
        elif phase == MicPhase.AI_RESPONDING:
            self.ui.set_standby(False)
            self.ui.set_state("THINKING")

    def _reset_orb_hide_deadline(self, seconds: float | None = None) -> None:
        """Start a fixed idle countdown for orb hide (not reset by ambient mic noise)."""
        if self.ui.siri_blocks_wake():
            return
        sec = seconds if seconds is not None else SIRI_IDLE_HIDE_SEC
        self._orb_hide_at = time.time() + sec
        self.ui.siri_schedule_hide(int(sec * 1000))

    def _clear_orb_hide_deadline(self) -> None:
        self._orb_hide_at = 0.0
        self.ui.siri_cancel_hide()

    def _set_ui_state(self, state: str):
        self._ui_state = state
        self.ui.apply_ui_state(state)
        self.ui.set_running_state(state in ("THINKING", "SPEAKING"))

    def _on_stop_command(self):
        """Forcefully stops the current agent loop, audio, and background tasks."""
        print("[NEO] Stop requested by user.")
        
        # Stop speaking
        self.player.stop()
        self.set_speaking(False)
        
        # In a Live API session, we can restart it to clear the context stream
        if self._loop and self.session:
            asyncio.run_coroutine_threadsafe(
                self._force_restart_live_session(),
                self._loop
            )
            
        self.ui.siri_cancel_hide()
        self._set_ui_state("STANDBY")

    async def _force_restart_live_session(self):
        """Cleanly drops and recreates the Gemini Live connection."""
        if self.session:
            try:
                await self.session.close()
            except Exception:
                pass
        self._reset_live_session_state()
        self.session = None



    def _go_standby(self):
        """Return to wake-word standby and hide the open mic."""
        self._clear_orb_hide_deadline()
        self._finish_processing(schedule_hide=False)
        self._user_spoke_this_turn = False
        self._followup_voice_engaged = False
        self._followup_voice_frames = 0
        self._voice_activity_frames = 0
        self._user_line_logged = False
        self._listen_deadline = 0.0
        self._followup_deadline = 0.0
        self._turn_finalize_pending = False
        self._neo_streaming = False
        with self._speaking_lock:
            self._is_speaking = False
        self.ui.set_speaking_active(False)
        self._wake_listen_blocked_until = time.time() + 0.25
        self._set_phase(MicPhase.STANDBY)
        self.ui.siri_hide_now()
        print("[NEO] Standby — say 'Hey Neo' or clap twice.")

    def _reset_live_session_state(self) -> None:
        """Clear stuck flags after a dropped live session so wake + orb recover."""
        self._processing = False
        self._finish_processing()
        self._neo_streaming = False
        self._turn_finalize_pending = False
        self._ack_spoken_this_turn = False
        self._display_command_turn = False
        self._wake_listen_blocked_until = 0.0
        with self._speaking_lock:
            self._is_speaking = False
        with self._out_buf_lock:
            self._live_out_buf = []
            self._live_in_buf = []
        self.ui.set_speaking_active(False)
        if self.audio_in_queue:
            while not self.audio_in_queue.empty():
                try:
                    self.audio_in_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        self._drain_out_queue()
        try:
            from core.conversation_compactor import reset_compactor

            reset_compactor()
        except Exception:
            pass

    def _drain_out_queue(self):
        if not self.out_queue:
            return
        while True:
            try:
                self.out_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _drain_incoming_audio(self):
        if not self.audio_in_queue:
            return
        while True:
            try:
                self.audio_in_queue.get_nowait()
            except Exception:
                break

    def _try_begin_offline_speech(self) -> bool:
        """Only one spoken output at a time (wake TTS). Blocks live model audio."""
        if not self._speech_exclusive.acquire(blocking=False):
            print("[NEO] Speech skipped — already talking.")
            return False
        self._offline_tts_active = True
        self._suppress_live_audio_until = time.time() + 90.0
        self._drain_incoming_audio()
        with self._speaking_lock:
            self._is_speaking = True
        return True

    def _end_offline_speech(self):
        with self._speaking_lock:
            self._is_speaking = False
        self._offline_tts_active = False
        self._suppress_live_audio_until = time.time() + 0.5
        try:
            self._speech_exclusive.release()
        except RuntimeError:
            pass
        self._wake_listen_blocked_until = max(
            self._wake_listen_blocked_until, time.time() + 0.8
        )
        self._wake_block_until = max(self._wake_block_until, time.time() + 0.8)

    @property
    def _mic_live(self) -> bool:
        if self.ui.manual_mute:
            return False
        if not self._smart_mode:
            return True
        return self._get_phase() in (MicPhase.USER_SPEAKING, MicPhase.FOLLOWUP)

    def _enqueue_pcm(self, pcm: bytes):
        if not self.out_queue:
            return
        try:
            self.out_queue.put_nowait({"data": pcm, "mime_type": "audio/pcm"})
        except asyncio.QueueFull:
            pass

    def _prime_wake_listen(self) -> None:
        """Called from the mic thread the instant wake/clap fires — open listen before _activate."""
        with self._phase_lock:
            if self._mic_phase not in (MicPhase.STANDBY, MicPhase.FOLLOWUP):
                return
            self._mic_phase = MicPhase.USER_SPEAKING
        self._listen_deadline = time.time() + LISTEN_TIMEOUT_SEC
        self._followup_voice_engaged = True
        self._followup_voice_frames = 0
        self._voice_activity_frames = 0

    def _handle_wake_trigger(self, source: str) -> None:
        """Wake/clap after idle hide — clear stale state and show the orb again."""
        if not self._smart_mode or self.ui.manual_mute:
            return
        now = time.time()
        if now < self._wake_listen_blocked_until:
            return

        self.ui.siri_wake()
        self._clear_orb_hide_deadline()
        self._finish_processing()
        self._cancel_think_filler()
        self._turn_finalize_pending = False
        self._neo_streaming = False
        self._user_spoke_this_turn = False
        self._followup_voice_engaged = False
        self._followup_voice_frames = 0
        self._voice_activity_frames = 0
        self._listen_deadline = 0.0
        self._followup_deadline = 0.0

        with self._speaking_lock:
            if self._is_speaking:
                self._is_speaking = False
        self.ui.set_speaking_active(False)
        self._drain_out_queue()
        self._drain_incoming_audio()
        # BARGE-IN: If NEO is speaking via offline TTS, unlock it
        if self._offline_tts_active:
            self._end_offline_speech()
        with self._phase_lock:
            self._mic_phase = MicPhase.STANDBY

        self.ui.siri_cancel_hide()
        if self._wake_listener:
            self._wake_listener.note_activate()

        self._prime_wake_listen()
        self._activate(source)
        self._flush_wake_prebuffer()

    def _flush_wake_prebuffer(self) -> None:
        """Send audio captured around the wake word (e.g. trailing 'hello') to the live model."""
        if not self._wake_listener or not self.out_queue:
            return
        for chunk in self._wake_listener.drain_prebuffer():
            if self._noise_gate:
                chunk = self._noise_gate.process(chunk)
            self._enqueue_pcm(chunk)

    def _activate(self, source: str):
        if not self._smart_mode or self.ui.manual_mute:
            return
        now = time.time()
        if now < self._wake_block_until:
            return
        # Barge-in: we removed speech exclusive lock check so user can interrupt

        self._wake_block_until = max(self._wake_block_until, now + 0.6)
        self._wake_listen_blocked_until = now + 0.15
        self._clear_orb_hide_deadline()

        if self._had_conversation:
            print(f"[NEO] Resume ({source})")
            self._ack_spoken_this_turn = False
            self._user_line_logged = False
            self._followup_voice_engaged = True
            self._voice_activity_frames = 0
            self.ui.siri_wake()
            self._set_phase(MicPhase.USER_SPEAKING)
            self._reset_orb_hide_deadline()
            self._flush_wake_prebuffer()
            return

        print(f"[NEO] Wake ({source})")
        self.ui.siri_wake()
        self._followup_voice_engaged = True
        self._followup_voice_frames = 0
        self._voice_activity_frames = 0
        self._ack_spoken_this_turn = False
        self._user_line_logged = False
        self._user_spoke_this_turn = False
        self._fast_path_turn_used = False
        if not self._session_greeted:
            self._start_session_greeting()
            return
        self._set_phase(MicPhase.USER_SPEAKING)
        self._reset_orb_hide_deadline()
        self._flush_wake_prebuffer()

    def _start_daily_news_check(self, *, force: bool = False) -> None:
        def run() -> None:
            from core.proactive_assistant import run_daily_news_briefing

            run_daily_news_briefing(self.ui, force=force)

        threading.Thread(target=run, daemon=True, name="NEO-daily-briefing").start()

    def _start_session_greeting(self) -> None:
        from core.proactive_assistant import stage_news_offer

        self._session_greeted = True
        greeting = _session_greeting()
        stage_news_offer()
        self._set_phase(MicPhase.WAKE_SPEAKING)
        self.ui.write_log(f"Neo: {greeting}")

        def play() -> None:
            began = self._try_begin_offline_speech()
            try:
                if began:
                    self.ui.set_speaking_active(True)
                    pcm = _synthesize_charon_greeting(_get_api_key(), greeting)
                    if pcm:
                        _play_pcm_blocking(pcm, on_chunk=self.ui.push_audio_levels)
            finally:
                if began:
                    self._end_offline_speech()
                    self.ui.set_speaking_active(False)
                if self._loop:
                    self._loop.call_soon_threadsafe(self._finish_session_greeting)
                else:
                    self._finish_session_greeting()

        threading.Thread(target=play, daemon=True, name="NEO-session-greeting").start()

    def _finish_session_greeting(self) -> None:
        if self._get_phase() == MicPhase.WAKE_SPEAKING:
            self._begin_followup(open_mic_immediately=True)

    def _force_listen(self):
        """Recover mic when user taps the button while NEO is processing."""
        print("[NEO] Force listen — reopening mic")
        self._finish_processing()
        with self._speaking_lock:
            self._is_speaking = False
        phase = self._get_phase()
        if phase in (MicPhase.AI_RESPONDING, MicPhase.WAKE_SPEAKING):
            self._begin_followup()
        elif self._smart_mode:
            self._set_phase(MicPhase.USER_SPEAKING)

    def _needs_immediate_ack(self, text: str) -> bool:
        t = text.strip()
        return len(t) > 8 and bool(_SLOW_ACK_RE.search(t))

    def _log_user_line(self, text: str):
        text = text.strip()
        if len(text.split()) < 1:
            return
        if text == self._last_user_log:
            return
        self._last_user_log = text
        self._user_line_logged = True
        self._had_conversation = True
        self.ui.write_log(f"You: {text}")

    def _tool_progress_eta(self, name: str) -> int:
        return _shared_tool_progress_eta(name)

    def _start_processing_keepalive(self):
        self._processing_keepalive_stop.clear()

        def loop():
            while not self._processing_keepalive_stop.wait(25.0):
                if not self._processing:
                    break
                # UI only — no repeated log lines or spoken duplicates

        threading.Thread(target=loop, daemon=True, name="NEO-keepalive").start()

    def _stop_processing_keepalive(self):
        self._processing_keepalive_stop.set()

    def _show_status_text(self, text: str):
        """Bar text only — no second voice (live model speaks when needed)."""
        if text and text.strip():
            self.ui.write_log_siri_compact(f"Neo: {text.strip()[:280]}")

    def _announce_tool_start(self, name: str, args: dict):
        line = _tool_status_line(name, args)
        self._processing_status_msg = line
        self.ui.write_activity(line)

    def _speak_tool_result_hints(self, result: str):
        """Show tool prompts on bar only — one voice via live model after."""
        r = (result or "").strip()
        if r.startswith("NEEDS_CONFIRM:") or r.startswith("NEEDS_INPUT:"):
            body = r.split(":", 1)[1].strip() if ":" in r else r
            if "Ask the user" in body:
                part = body.split("Ask the user", 1)[-1].strip().strip('"')
                self._show_status_text(part[:220])
            else:
                self._show_status_text(body[:180])

    def _enter_processing(self, eta_sec: int = 15, label: str = ""):
        """Mute mic and show progress while NEO works."""
        self.ui.siri_cancel_hide()
        self._processing = True
        now = time.time()
        self._wake_block_until = max(self._wake_block_until, now + 180.0)
        self._wake_listen_blocked_until = max(self._wake_listen_blocked_until, now + 180.0)
        phase = self._get_phase()
        if phase in (MicPhase.USER_SPEAKING, MicPhase.FOLLOWUP):
            self._drain_out_queue()
            self._set_phase(MicPhase.AI_RESPONDING)
        self.ui.start_log_progress(eta_sec, label or self._processing_status_msg or "Working…")
        self._start_processing_keepalive()

    def _trigger_product_images(self, query: str):
        summary = self._last_search_result or self._last_neo_reply
        if not summary and not query:
            return

        print(f"[NEO] 🖼 Opening images: {query!r}")
        if not self._ack_spoken_this_turn:
            self._enter_processing(25)
            self.ui.write_log("Neo: One moment.")
        else:
            self._enter_processing(25)
        self.ui.show_visuals(
            summary or "",
            query,
            on_done=self._finish_processing,
        )

    def _maybe_auto_show_images(self, text: str):
        if not _SHOW_IMAGES_RE.search(text):
            return
        summary = self._last_search_result or self._last_neo_reply
        if not summary:
            return
        threading.Thread(
            target=self._trigger_product_images,
            args=(text,),
            daemon=True,
        ).start()

    def _cancel_think_filler(self):
        self._think_filler_stop.set()

    def _schedule_think_filler(self, user_text: str):
        """Status only — offline filler TTS can starve the live WebSocket recv loop."""
        self._cancel_think_filler()
        self._think_filler_stop = threading.Event()
        stop = self._think_filler_stop
        text = (user_text or "").strip()

        def run():
            if stop.wait(0.85):
                return
            if self._ack_spoken_this_turn or self._processing:
                return
            with self._speaking_lock:
                if self._is_speaking or self._offline_tts_active:
                    return
            phrase = _filler_from_user_text(text)
            if not phrase:
                return
            self._ack_spoken_this_turn = True
            self.ui.write_activity(phrase)

        threading.Thread(target=run, daemon=True, name="NEO-think").start()

    def _fast_path_duplicate_result(self, tool_name: str, args: dict) -> str | None:
        """If fast path already ran this tool+args recently, return cached result."""
        key = _fast_path_args_key(tool_name, args)
        with self._fast_path_guard_lock:
            g = self._fast_path_guard
            if not g or time.time() > g.get("until", 0):
                return None
            if g.get("key") == key:
                return g.get("result", "Done.")
        return None

    def _register_fast_path(self, tool_name: str, args: dict, result: str) -> None:
        with self._fast_path_guard_lock:
            self._fast_path_guard = {
                "key": _fast_path_args_key(tool_name, args),
                "tool": tool_name,
                "until": time.time() + 15.0,
                "result": result,
            }
        self._fast_path_turn_used = True
        self._suppress_live_audio_until = max(
            self._suppress_live_audio_until, time.time() + 4.0,
        )

    def _fast_path_confirmation(self, tool_name: str, args: dict, result: str) -> str:
        if tool_name == "open_app":
            app = (args or {}).get("app_name", "that")
            return f"Opened {app}."
        if tool_name == "system_control":
            return result.split(".")[0][:80] if result else "Done."
        return (result or "Done.").split("\n")[0][:100]

    def _wrap_user_turn(self, text: str) -> str:
        """Augment user text with action context and relevant memories."""
        prefix = ""
        try:
            from core.action_context import format_for_prompt

            ctx = format_for_prompt()
            if ctx:
                prefix += ctx
        except Exception:
            pass

        if text and len(text) > 5:
            try:
                from core.memory_rag import format_memory_for_prompt

                mem_block = format_memory_for_prompt(text, top_k=5)
                if mem_block:
                    prefix += "\n" + mem_block
            except Exception:
                pass

        return f"{prefix}{text}" if prefix else text

    def _try_open_context_link(self, text: str, source: str) -> bool:
        try:
            from core.action_context import resolve_link, open_message
            from actions.browser_control import browser_control
        except Exception:
            return False

        url = resolve_link(text)
        if not url:
            return False

        if not self._user_line_logged:
            self._log_user_line(text)
        self._enter_processing(10, "Opening link…")
        try:
            browser_control({"action": "go_to", "url": url})
            msg = open_message(url)
        except Exception as e:
            msg = f"Could not open the link: {e}"
        self.ui.write_log(f"Neo: {msg}")
        self._set_phase(MicPhase.AI_RESPONDING)
        self._ack_spoken_this_turn = True
        self._finish_processing()
        print(f"[NEO] Context link opened ({source}): {url[:80]}")
        return True

    def _try_consume_fast_path(self, text: str, source: str) -> bool:
        """Run tool locally without sending the user message to Gemini Live."""
        if not text.strip():
            return False
        if self._try_open_context_link(text, source):
            return True
        if not _hybrid_fast_path_enabled():
            return False
        if self._run_local_system_control(text, notify_model=False):
            return True
        ctx = _HYBRID_ORCHESTRATOR.build_context(self)
        fast = _HYBRID_ORCHESTRATOR.try_fast_path(text, ctx)
        if not fast or not fast.ok:
            return False

        decision = _HYBRID_ORCHESTRATOR.router.route(text)
        tool_name = decision.tool_name or fast.tool_name
        tool_args = decision.tool_args or {}
        if not tool_name:
            return False

        self._register_fast_path(tool_name, tool_args, fast.text)
        if not self._user_line_logged:
            self._log_user_line(text)
        self.ui.write_log(f"Neo: {fast.text}")
        self._set_phase(MicPhase.AI_RESPONDING)
        self._ack_spoken_this_turn = True
        self._cancel_think_filler()
        # TTS filler disabled — Gemini Live handles response natively
        # confirm = self._fast_path_confirmation(tool_name, tool_args, fast.text)
        print(f"[NEO] Fast path complete ({source}) — Gemini tool calls skipped for this command")
        return True


    def _try_goal_dispatcher(self, text: str) -> bool:
        """Catch multi-task input and dispatch in parallel BEFORE Gemini.

        Splits requests like 'check email AND tell me the weather AND open Chrome'
        into independent goals and runs them in parallel, bypassing the live session.
        """
        try:
            from core.goal_dispatcher import split_goals
        except ImportError:
            return False
        goals = split_goals(text)
        if len(goals) < 2:
            return False
        print(f"[NEO] 🎯 GoalDispatcher: detected {len(goals)} goals")
        try:
            from core.goal_dispatcher import get_dispatcher
            from hybrid.types import ExecutionContext
            ctx = ExecutionContext(
                ui=self.ui,
                speak=getattr(self, "notify_user", getattr(self, "speak", None)),
            )
            result = get_dispatcher().dispatch(text, ctx)
            if not self._user_line_logged:
                self._log_user_line(text)
            self._set_phase(MicPhase.AI_RESPONDING)
            self._ack_spoken_this_turn = True
            self.ui.write_log(f"Neo: {result.summary[:500]}")
            print(f"[NEO] ✅ GoalDispatcher: {len(result.results)} goals completed")
            return True
        except Exception as exc:
            print(f"[NEO] ⚠️ GoalDispatcher failed: {exc}")
            return False

    def _speak_tool_filler(self, name: str, args: dict | None = None):
        if self._ack_spoken_this_turn:
            return
        phrase = _tool_filler_phrase(name, args)
        if phrase:
            self._ack_spoken_this_turn = True
            # TTS filler disabled — Gemini Live WebSocket handles response natively
            # self._speak_filler_async(phrase)

    def _finish_processing(self, *, schedule_hide: bool = True):
        self._processing = False
        self._stop_processing_keepalive()
        self._processing_status_msg = ""
        self.ui.stop_log_progress()
        phase = self._get_phase()
        if (
            schedule_hide
            and phase in (MicPhase.FOLLOWUP, MicPhase.USER_SPEAKING)
            and not self.ui.siri_blocks_wake()
        ):
            self._reset_orb_hide_deadline()

    def _speak_quick_ack(self, user_text: str = ""):
        if self._ack_spoken_this_turn:
            return
        phrase = _filler_from_user_text(user_text or self._last_user_log)
        if not phrase:
            return
        self._ack_spoken_this_turn = True
        self.ui.write_activity(phrase)

    def _extend_listen_on_voice(self, pcm: bytes) -> None:
        """Reset silence timer only after sustained speech (ignore brief noise)."""
        phase = self._get_phase()
        if phase not in (MicPhase.USER_SPEAKING, MicPhase.FOLLOWUP):
            return
        samples = np.frombuffer(pcm, dtype=np.int16)
        if samples.size == 0:
            return
        peak = float(np.max(np.abs(samples.astype(np.int32))))
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
        if peak >= 750 or rms >= 420:
            self._voice_activity_frames += 1
        else:
            self._voice_activity_frames = max(0, self._voice_activity_frames - 1)
        if self._voice_activity_frames >= 3:
            now = time.time()
            if phase == MicPhase.USER_SPEAKING:
                self._listen_deadline = now + LISTEN_TIMEOUT_SEC
            else:
                self._followup_deadline = now + FOLLOWUP_SECONDS
            self._followup_voice_engaged = True
            self._user_spoke_this_turn = True

    def _mark_neo_reply_started(self) -> None:
        """Pause silence timeout while NEO is speaking."""
        self._cancel_think_filler()
        phase = self._get_phase()
        if phase in (MicPhase.USER_SPEAKING, MicPhase.FOLLOWUP):
            with self._phase_lock:
                self._mic_phase = MicPhase.AI_RESPONDING
            self._listen_deadline = 0.0
            self._followup_deadline = 0.0
            self.ui.set_standby(False)
            self.ui.set_state("SPEAKING")

    def _followup_voice_gate(self, pcm: bytes) -> bool:
        """In FOLLOWUP, only stream mic after real speech (blocks phantom replies)."""
        if self._get_phase() != MicPhase.FOLLOWUP:
            return True
        if self._followup_voice_engaged:
            return True
        samples = np.frombuffer(pcm, dtype=np.int16)
        if samples.size == 0:
            return False
        peak = float(np.max(np.abs(samples)))
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
        if peak >= 550 or rms >= 320:
            self._followup_voice_frames += 1
        else:
            self._followup_voice_frames = max(0, self._followup_voice_frames - 1)
        if self._followup_voice_frames >= 2:
            self._followup_voice_engaged = True
            self._user_spoke_this_turn = True
            self._followup_deadline = time.time() + FOLLOWUP_SECONDS
            self._set_phase(MicPhase.USER_SPEAKING)
            return True
        return False

    def _begin_followup(self, open_mic_immediately: bool = False):
        self._followup_deadline = time.time() + FOLLOWUP_SECONDS
        self._followup_voice_frames = 0
        self._followup_voice_engaged = open_mic_immediately
        self._ack_spoken_this_turn = False
        self._user_line_logged = False
        self._set_phase(MicPhase.FOLLOWUP)
        self.ui.siri_wake()
        self._reset_orb_hide_deadline()

    def _on_stopped_speaking(self):
        if not self._smart_mode:
            if self._mic_live:
                self.ui.set_state("LISTENING")
            return
        phase = self._get_phase()
        if phase == MicPhase.WAKE_SPEAKING:
            self._ack_spoken_this_turn = False
            self._user_line_logged = False
            self._begin_followup(open_mic_immediately=True)
        elif phase in (MicPhase.AI_RESPONDING, MicPhase.USER_SPEAKING, MicPhase.FOLLOWUP):
            if not self._processing:
                self._begin_followup(open_mic_immediately=True)

    def _handle_turn_complete(self, user_text: str, model_text: str = ""):
        if not self._smart_mode:
            return
        phase = self._get_phase()
        user_text = user_text.strip()
        word_count = len(user_text.split())

        if phase == MicPhase.FOLLOWUP:
            if word_count < _MIN_USER_WORDS or not self._followup_voice_engaged:
                print("[NEO] Ignoring empty follow-up turn (phantom audio).")
                return

        if phase == MicPhase.USER_SPEAKING and (user_text or self._user_spoke_this_turn):
            if user_text and not self._user_line_logged:
                self._log_user_line(user_text)
            self._user_spoke_this_turn = False
            self._user_line_logged = False
            if self._get_phase() == MicPhase.USER_SPEAKING:
                self._set_phase(MicPhase.AI_RESPONDING)
        elif phase == MicPhase.AI_RESPONDING and model_text:
            pass  # Standby after response — no open-mic follow-up (prevents self-replies)

    def _finalize_turn_output(self, user_text: str = ""):
        """Finish log + follow-up after audio drained and late transcription settled."""
        with self._out_buf_lock:
            full_out = _join_transcript(self._live_out_buf)
            full_in = user_text or " ".join(self._live_in_buf).strip()
            self._live_out_buf = []
            self._live_in_buf = []

        self._turn_finalize_pending = False

        phase = self._get_phase()
        phantom = (
            phase == MicPhase.FOLLOWUP
            and (
                len(full_in.split()) < _MIN_USER_WORDS
                or not self._followup_voice_engaged
            )
        )
        if phantom:
            print("[NEO] Ignoring phantom turn (no real user speech).")
            self._live_out_buf = []
            self._live_in_buf = []
            self._neo_streaming = False
            self._user_spoke_this_turn = False
            self._followup_voice_engaged = False
            self._drain_incoming_audio()
            self._drain_out_queue()
            self.ui.stop_log_progress()
            self._go_standby()
            return

        if full_in and not self._user_line_logged:
            self._log_user_line(full_in)

        system_handled = False
        if full_in and len(full_in.split()) >= 2:
            system_handled = self._run_local_system_control(
                full_in, notify_model=not self._system_handled_turn
            )

        phase = self._get_phase()
        if full_out and phase not in (MicPhase.WAKE_SPEAKING,):
            if not _is_ack_only(full_out):
                self._last_neo_reply = full_out
            if not system_handled:
                if self._neo_streaming:
                    self.ui.finish_neo_stream(full_out)
                elif not _is_ack_only(full_out):
                    self.ui.write_log(f"Neo: {full_out}")
            elif self._neo_streaming and full_out:
                self.ui.finish_neo_stream(full_out)
            self._processing = False

        if self._display_command_turn and full_out and not system_handled:
            self.ui.show_command_response(full_out)
            self._speak_command_ready_notice()
        self._display_command_turn = False

        self._handle_turn_complete(full_in, full_out)

        # ── Intelligence loop: observe, compact, suggest ──
        if full_in and full_out and not phantom and not system_handled:
            self._run_memory_intelligence(full_in, full_out)
            try:
                from core.proactive_assistant import analyze_turn

                proactive = analyze_turn(full_in, full_out)
                if proactive:
                    self.ui.write_log(f"Neo: {proactive}")
            except Exception as exc:
                print(f"[Proactive] Suggestion skipped: {exc}")
            try:
                from core.memory_suggestions import get_suggestion

                suggestion = get_suggestion()
                if suggestion:
                    self.ui.write_log(f"Neo: 💡 {suggestion}")
            except Exception:
                pass

        self._user_line_logged = False
        self._ack_spoken_this_turn = False
        self._neo_streaming = False
        self._system_handled_turn = False

    def _run_memory_intelligence(self, user_text: str, neo_response: str) -> None:
        """Background intelligence: observe facts, compact conversation."""
        try:
            from core.memory_observer import observe_turn

            observe_turn(user_text, neo_response)
        except Exception:
            pass

        try:
            from core.conversation_compactor import get_compactor

            compactor = get_compactor()
            compactor.record_turn(user_text, neo_response)
            if compactor.should_summarize():
                compactor.summarize(blocking=False)
        except Exception:
            pass

    def _try_begin_followup(self):
        """After NEO finishes speaking, keep mic open briefly for a reply."""
        phase = self._get_phase()
        if phase not in (MicPhase.AI_RESPONDING, MicPhase.USER_SPEAKING) or self._processing:
            return
        with self._speaking_lock:
            if self._is_speaking:
                return
        if self.out_queue and not self.out_queue.empty():
            return
        if self.audio_in_queue and not self.audio_in_queue.empty():
            return
        self._begin_followup(open_mic_immediately=True)

    def _run_local_system_control(self, user_text: str, *, notify_model: bool = True) -> bool:
        """Execute volume/brightness locally so the model cannot fake success."""
        from actions.system_control import resolve_command_from_text, system_control

        user_text = user_text.strip()
        if not user_text or not _SYSTEM_CONTROL_RE.search(user_text):
            return False

        last = self._last_system_command or None
        cmd = resolve_command_from_text(user_text, last)
        if not cmd:
            return False

        if self._system_handled_turn:
            return True

        with self._local_system_lock:
            steps = 2 if any(
                p in user_text.lower()
                for p in ("more", "again", "even", "further", "keep")
            ) else 1
            print(f"[NEO] 🔊 Local system_control: {cmd!r} ← {user_text!r} (steps={steps})")
            result = system_control(
                {"command": cmd, "steps": steps, "last_command": last or ""}
            )
            self._last_system_command = cmd
            self._system_handled_turn = True

        self.ui.write_log(f"Neo: {result}")
        self._set_phase(MicPhase.AI_RESPONDING)

        if notify_model and self._loop and self.session:
            asyncio.run_coroutine_threadsafe(
                self.session.send_client_content(
                    turns={
                        "parts": [
                            {
                                "text": (
                                    f"[SYSTEM_DONE] Ran {cmd}. Result: {result}\n"
                                    "Reply in ONE short sentence using ONLY this result. "
                                    "Do NOT call tools. Never claim success if Result starts with FAILED."
                                )
                            }
                        ]
                    },
                    turn_complete=True,
                ),
                self._loop,
            )
        return True

    def _on_input_transcription(self, text: str, finished: bool = False):
        if not self._smart_mode or not text:
            return
        phase = self._get_phase()
        if phase in (MicPhase.USER_SPEAKING, MicPhase.FOLLOWUP):
            if len(text.split()) >= 1:
                self._user_spoke_this_turn = True
                self._listen_deadline = 0
                self.ui.siri_set_prompt("I'm listening…")
                self.ui.siri_cancel_hide()
                self._reset_orb_hide_deadline()
        if finished and phase in (MicPhase.USER_SPEAKING, MicPhase.FOLLOWUP):
            min_words = 1 if phase == MicPhase.USER_SPEAKING else _MIN_USER_WORDS
            if len(text.split()) >= min_words:
                if self._try_proactive_confirmation(text, notify_model=True):
                    return
                from core.response_presentation import is_command_request

                self._display_command_turn = is_command_request(text)
                if self._display_command_turn:
                    self._suppress_live_audio_until = time.time() + 90.0
                    self._drain_incoming_audio()
                if self._try_consume_fast_path(text, "voice"):
                    return
                if self._try_goal_dispatcher(text):
                    return
                self._log_user_line(text)
                if self._needs_immediate_ack(text):
                    self._speak_quick_ack(text)
                    self._set_phase(MicPhase.AI_RESPONDING)
                else:
                    self._schedule_think_filler(text)
                self._maybe_auto_show_images(text)

    def _on_text_command(self, text: str) -> bool:
        if not self._loop or not self.session:
            return False
        text = text.strip()
        if not text:
            return False
        if self._try_proactive_confirmation(text, notify_model=True):
            return True
        from core.response_presentation import is_command_request

        self._display_command_turn = is_command_request(text)
        if self._display_command_turn:
            self._suppress_live_audio_until = time.time() + 90.0
            self._drain_incoming_audio()
        # The UI's _send() already logged "You: ..." — prevent duplicate
        self._user_line_logged = True
        self._last_user_log = text
        if self._run_local_system_control(text):
            return True
        if self._try_consume_fast_path(text, "typed"):
            return True
        if self._try_goal_dispatcher(text):
            return True
        if self._smart_mode and self._get_phase() == MicPhase.STANDBY:
            # Typed command — do not open the live mic or show "listening"
            self.ui.siri_wake()
            self._wake_listen_blocked_until = time.time() + 12.0
        elif self._smart_mode:
            self._drain_out_queue()
            self.ui.set_mic_live(self._mic_live)
        typed_turn = (
            "[TYPED_CHAT_MESSAGE]\n"
            "This is an intentional typed message, not a wake-word event. "
            "Respond directly, including to greetings.\n"
            + self._wrap_user_turn(text)
        )
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": typed_turn}]},
                turn_complete=True
            ),
            self._loop
        )
        return True

    def _try_proactive_confirmation(self, text: str, *, notify_model: bool) -> bool:
        from core.proactive_assistant import consume_reply

        pending = consume_reply(text)
        if pending is None:
            return False
        kind, parameters = pending
        if not self._user_line_logged:
            self._log_user_line(text)
        if kind == "cancelled":
            result = "Okay, I won't set it."
        elif kind == "reminder":
            from actions.reminder import reminder

            result = reminder(parameters)
        elif kind == "news":
            self._start_daily_news_check(force=True)
            result = "I'll check and only show you something if it genuinely needs attention."
        else:
            return False
        self.ui.write_log(f"Neo: {result}")
        self._set_phase(MicPhase.AI_RESPONDING)
        if notify_model and self._loop and self.session:
            asyncio.run_coroutine_threadsafe(
                self.session.send_client_content(
                    turns={"parts": [{"text": (
                        f"[SYSTEM_DONE] Proactive follow-up result: {result}\n"
                        "Acknowledge in one short sentence. Do not call tools."
                    )}]},
                    turn_complete=True,
                ),
                self._loop,
            )
        return True

    def set_speaking(self, value: bool, *, block_wake_after: bool = True):
        with self._speaking_lock:
            self._is_speaking = value
        self.ui.set_speaking_active(value)
        if value:
            self.ui.siri_wake()
        else:
            if block_wake_after and self._smart_mode:
                self._wake_listen_blocked_until = max(
                    self._wake_listen_blocked_until, time.time() + 0.9
                )
            self._on_stopped_speaking()

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def _speak_command_ready_notice(self) -> None:
        """Speak a short notice without reading terminal syntax aloud."""
        if not self._try_begin_offline_speech():
            return

        def play() -> None:
            try:
                pcm = _synthesize_charon_speech(
                    _get_api_key(), "I've put the command on screen."
                )
                if pcm:
                    _play_pcm_blocking(pcm, on_chunk=self.ui.push_audio_levels)
            finally:
                self._end_offline_speech()

        threading.Thread(target=play, daemon=True, name="NEO-command-notice").start()

    def notify_user(self, text: str, interrupt: bool = False):
        """Used by orchestrator and background tasks to push updates to the UI chat log."""
        if hasattr(self, "ui") and self.ui:
            self.ui.write_log(f"NEO: {text}")
        if not self._mic_live:
            # Optionally speak offline if the user isn't talking
            self.speak(text)

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        print(f"[NEO] ERR: {tool_name} — {short}")
        self._show_status_text(f"{tool_name} had an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime
        from core import paths

        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        parts = [time_ctx]
        parts.append(sys_prompt)
        
        # Inject recent conversation history
        try:
            mem_dir = paths.base_dir() / "memory"
            hist_file = mem_dir / "conversation_history.txt"
            if hist_file.exists():
                lines = hist_file.read_text(encoding="utf-8").strip().split('\n')
                if lines:
                    history_str = "[RECENT CONVERSATION HISTORY]\n"
                    for line in lines[-20:]: # Inject last 20 messages
                        try:
                            msg = json.loads(line)
                            role = "User" if msg["role"] == "user" else "NEO"
                            history_str += f"{role}: {msg['text']}\n"
                        except Exception:
                            continue
                    history_str += "\n(The above is your recent memory. Resume the conversation naturally from here.)\n\n"
                    parts.append(history_str)
        except Exception as e:
            print(f"[Memory] Failed to load conversation history: {e}")

        # Inject NeoProjects structure
        try:
            neo_proj = Path.home() / "Desktop" / "NeoProjects"
            if neo_proj.exists():
                proj_files = []
                for root, dirs, files in os.walk(neo_proj):
                    dirs[:] = [d for d in dirs if not d.startswith('.')] # skip hidden
                    rel_root = Path(root).relative_to(neo_proj)
                    for f in files:
                        if not f.startswith('.'):
                            proj_files.append(str(rel_root / f) if str(rel_root) != "." else f)
                
                if proj_files:
                    proj_str = "[WORKSPACE CONTEXT: Desktop/NeoProjects]\n"
                    proj_str += "The user has an active workspace directory at ~/Desktop/NeoProjects containing:\n"
                    proj_str += "\n".join(f"- {f}" for f in proj_files[:100]) # cap at 100 files
                    if len(proj_files) > 100:
                        proj_str += "\n... and more."
                    proj_str += "\n\n"
                    parts.append(proj_str)
        except Exception as e:
            pass

        try:
            from actions.skill_loader import build_skills_catalog

            catalog = build_skills_catalog()
            if catalog:
                parts.append(catalog + "\n\n")
        except Exception as e:
            print(f"[SkillLoader] Failed to build skills catalog: {e}")

        try:
            from core.action_context import format_for_prompt

            action_ctx = format_for_prompt()
            if action_ctx:
                parts.append(action_ctx)
        except Exception as e:
            print(f"[ActionContext] Failed to load action context: {e}")

        try:
            from config import get_default_location

            loc = get_default_location()
            if loc:
                parts.append(
                    f"[USER LOCATION]\n"
                    f"The user's default city is: {loc}\n"
                    f"For weather with no city specified, use weather_report without a city.\n\n"
                )
        except Exception:
            pass

        # ── Inject ranked stored memories ──
        try:
            from core.memory_ext import get_session_memory_block
            from core.memory_importance import format_importance_for_prompt

            raw_memories = get_session_memory_block()
            # Rebuild with importance ranking by fetching raw list
            from core.memory_ext import get_all_memories
            all_mem = get_all_memories(limit=50)
            if all_mem:
                ranked = format_importance_for_prompt(all_mem)
                if ranked:
                    parts.append(ranked + "\n\n")
        except Exception:
            pass

        # ── Inject compressed conversation history ──
        try:
            from core.conversation_compactor import get_compactor

            conv_ctx = get_compactor().get_context_block()
            if conv_ctx:
                parts.append(conv_ctx + "\n\n")
        except Exception:
            pass

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": CORE_TOOL_DECLARATIONS}],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=_live_voice_name()
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        dup = self._fast_path_duplicate_result(name, args)
        if dup is not None:
            print(f"[NEO] ⏭️ Skipping duplicate {name} (fast path already handled)")
            if self._mic_live:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": dup, "fast_path": True},
            )

        print(f"[NEO] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")
        self._cancel_think_filler()

        show_progress = name in _SLOW_TOOLS
        if show_progress and not self._processing:
            label = _tool_status_line(name, args)
            self._announce_tool_start(name, args)
            self._speak_tool_filler(name, args)
            self._enter_processing(self._tool_progress_eta(name), label=label)
        elif show_progress and self._processing:
            self._announce_tool_start(name, args)

        if name == "shutdown_neo":
            self._show_status_text("Goodbye.")

        # ── Circuit breaker check ──
        try:
            from core.error_recovery import is_circuit_open
            if is_circuit_open(name):
                from core.error_recovery import _registry
                msg = _registry.get_open_message(name)
                print(f"[NEO] 🛑 Circuit OPEN for {name}")
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": msg},
                )
        except Exception:
            pass

        try:
            result = await _HYBRID_ORCHESTRATOR.execute_tool_for_live(
                fc,
                self,
                on_finish=lambda n, res: self._after_tool_result(
                    n, res, show_progress=show_progress,
                ),
            )
            # Record success
            try:
                from core.error_recovery import _dashboard
                _dashboard.record_success(name)
            except Exception:
                pass
            return result
        except Exception as e:
            traceback.print_exc()
            self.speak_error(name, e)
            # ── Record error for circuit breaker ──
            try:
                from core.error_recovery import record_error
                guidance = record_error(name, e)
                if guidance.get("circuit_open"):
                    self._show_status_text(
                        f"Tool '{name}' temporarily blocked — too many failures."
                    )
                elif guidance.get("fallback"):
                    print(f"[NEO] ⚠️ {name} failed, fallback: {guidance['fallback']}")
            except Exception:
                pass
            if show_progress:
                self._finish_processing()
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": f"Tool '{name}' failed: {e}"},
            )

    def _after_tool_result(self, name: str, result, *, show_progress: bool) -> None:
        from hybrid.types import ToolResult

        if not isinstance(result, ToolResult):
            return
        text = result.text or "Done."
        marker = "INSTALL_CONFIRMATION_JSON:"
        if marker in text:
            try:
                card = json.loads(text.split(marker, 1)[1].splitlines()[0])
                self.ui.show_install_confirmation(
                    str(card["title"]), str(card["source"]), str(card["command"])
                )
                result.text = (
                    f"A verified {card['source']} install is ready and the confirmation "
                    f"card is visible. Ask exactly: Install {card['title']}?"
                )
                text = result.text
            except (KeyError, TypeError, ValueError):
                pass
        if text.startswith("CANCELLED_COMMAND:"):
            match = re.search(r"```(?:shell|bash|powershell)?\s*\n(.*?)\n```", text, re.S)
            if match:
                self.ui.show_command_response(f"```shell\n{match.group(1).strip()}\n```")
            result.text = (
                "The terminal command was not run and is already visible on screen. "
                "Reply exactly: I've put the command on screen."
            )
            text = result.text
        if show_progress:
            self._finish_processing()
        self._speak_tool_result_hints(text)
        if show_progress and text and not text.startswith("Tool '"):
            self._show_status_text(text.split("\n")[0][:180])
        print(f"[NEO] 📤 {name} → {text[:80]}")
        try:
            from core.memory_suggestions import record_tool_call
            record_tool_call(name)
        except Exception:
            pass

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            if not self.session:
                continue
            try:
                await self.session.send_realtime_input(media=msg)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[NEO] Mic send stopped: {e}")
                return

    async def _handle_tool_calls(self, tool_call) -> None:
        """Run tools off the receive loop so the live WebSocket stays alive."""
        fn_responses: list[types.FunctionResponse] = []
        try:
            for fc in tool_call.function_calls:
                fn_responses.append(await self._execute_tool(fc))
            if not fn_responses or not self.session:
                return
            lock = self._tool_send_lock
            if lock:
                async with lock:
                    await self.session.send_tool_response(
                        function_responses=fn_responses
                    )
            else:
                await self.session.send_tool_response(
                    function_responses=fn_responses
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[NEO] ❌ Tool batch: {e}")
            traceback.print_exc()
            self._reset_live_session_state()

    async def _listen_audio(self):
        print("[NEO] 🎤 Mic started")
        loop = asyncio.get_event_loop()
        in_dev, _ = _default_audio_devices()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                neo_speaking = self._is_speaking
            pcm = indata.tobytes()

            wake_phase = self._get_phase()
            if (
                self._wake_listener
                and wake_phase in (MicPhase.STANDBY, MicPhase.FOLLOWUP)
                and not self.ui.siri_blocks_wake()
                and not self.ui.manual_mute
                and not self._processing
                and not self._offline_tts_active
                and not self._speech_exclusive.locked()
                and not neo_speaking
                and time.time() >= self._wake_listen_blocked_until
            ):
                trigger = self._wake_listener.feed(pcm)
                if trigger:
                    self.ui.siri_wake()
                    loop.call_soon_threadsafe(self._handle_wake_trigger, trigger)

            if self._mic_live and not neo_speaking:
                if self._noise_gate:
                    pcm = self._noise_gate.process(pcm)
                self._extend_listen_on_voice(pcm)
                if not self._followup_voice_gate(pcm):
                    return
                self.ui.push_audio_levels(_pcm_to_bands(pcm))
                loop.call_soon_threadsafe(self._enqueue_pcm, pcm)

        kwargs: dict = {
            "samplerate": SEND_SAMPLE_RATE,
            "channels": CHANNELS,
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
                dev_label = in_dev if in_dev is not None else "default"
                print(f"[NEO] Mic stream open (device={dev_label})")
                while True:
                    await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[NEO] Mic disabled: {e} (Text-only mode)")
            try:
                devices = sd.query_devices()
                inputs = [
                    f"  [{i}] {d.get('name', '?')}"
                    for i, d in enumerate(devices)
                    if int(d.get("max_input_channels", 0)) > 0
                ]
                if inputs:
                    print("[NEO] Available inputs:\n" + "\n".join(inputs[:8]))
                else:
                    print("[NEO] No input devices found — check Windows Sound settings.")
            except Exception:
                pass
            while True:
                await asyncio.sleep(3600)
        finally:
            sd.stop()

    async def _receive_audio(self):
        print("[NEO] 👂 Recv started")

        try:
            while True:
                async for response in self.session.receive():

                    audio_data = _extract_live_audio(response)
                    if audio_data:
                        if time.time() < self._suppress_live_audio_until:
                            continue
                        if self._turn_done_event and self._turn_done_event.is_set():
                            self._turn_done_event.clear()
                        self.audio_in_queue.put_nowait(audio_data)

                    if response.server_content:
                        sc = response.server_content

                        phase = self._get_phase()

                        output_transcription = getattr(sc, "output_transcription", None)
                        if (
                            output_transcription
                            and output_transcription.text
                            and phase != MicPhase.WAKE_SPEAKING
                        ):
                            txt = _clean_transcript(output_transcription.text)
                            if txt:
                                with self._out_buf_lock:
                                    self._live_out_buf = _merge_transcript(
                                        self._live_out_buf, txt
                                    )
                                    live = _join_transcript(self._live_out_buf)
                                if live and not (
                                    _is_ack_only(live) and self._ack_spoken_this_turn
                                ):
                                    self.ui.stream_neo(live)
                                    self._neo_streaming = True
                        elif sc.model_turn and sc.model_turn.parts:
                            if phase != MicPhase.WAKE_SPEAKING:
                                for p in sc.model_turn.parts:
                                    if hasattr(p, "text") and p.text:
                                        with self._out_buf_lock:
                                            self._live_out_buf.append(p.text)
                                        live = _join_transcript(self._live_out_buf)
                                        if live and not (
                                            _is_ack_only(live) and self._ack_spoken_this_turn
                                        ):
                                            self.ui.stream_neo(live)
                                            self._neo_streaming = True

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            finished = bool(
                                getattr(sc.input_transcription, "finished", False)
                            )
                            if txt:
                                with self._out_buf_lock:
                                    self._live_in_buf.append(txt)
                                self._on_input_transcription(txt, finished=finished)

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()
                            self._turn_finalize_pending = True
                            # Do NOT finalize log here — wait for audio + late text

                    if response.tool_call:
                        # Text emitted before a tool call is provisional planning,
                        # not the answer. Remove it so only the post-tool result is
                        # shown in either chat surface.
                        with self._out_buf_lock:
                            self._live_out_buf = []
                        self._neo_streaming = False
                        self._turn_finalize_pending = False
                        self.ui.cancel_neo_stream()
                        asyncio.create_task(
                            self._handle_tool_calls(response.tool_call),
                            name=f"NEO-tool-{response.tool_call.function_calls[0].name if response.tool_call.function_calls else 'batch'}",
                        )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err = str(e).lower()
            if any(
                tok in err
                for tok in ("1006", "1008", "1011", "internal error", "keepalive", "closed", "abnormal closure")
            ):
                print(f"[NEO] Live session dropped ({err[:20]}) — reconnecting.")
            else:
                print(f"[NEO] ❌ Recv: {e}")
                traceback.print_exc()
            self._reset_live_session_state()
            self.set_speaking(False, block_wake_after=False)
            return

    async def _play_audio(self):
        print("[NEO] 🔊 Play started")
        stream: sd.RawOutputStream | None = None
        play_rate = RECEIVE_SAMPLE_RATE
        playback_ok = True

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1,
                    )
                except asyncio.TimeoutError:
                    if time.time() < self._suppress_live_audio_until:
                        continue
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                        if self._turn_finalize_pending:
                            await asyncio.sleep(0.55)
                            if self.audio_in_queue.empty():
                                self._finalize_turn_output()
                        self._try_begin_followup()
                    continue

                if stream is None:
                    if not playback_ok:
                        continue
                    try:
                        stream, play_rate = await asyncio.to_thread(_open_playback_stream)
                    except Exception as e:
                        playback_ok = False
                        print(f"[NEO] ❌ Play: {e}")
                        print("[NEO] Speaker unavailable — check System Settings → Sound output.")
                        continue

                if time.time() < self._suppress_live_audio_until:
                    continue

                if self._get_phase() in (MicPhase.USER_SPEAKING, MicPhase.FOLLOWUP):
                    self._mark_neo_reply_started()

                self.set_speaking(True)
                if play_rate != RECEIVE_SAMPLE_RATE:
                    chunk = _resample_pcm16(chunk, RECEIVE_SAMPLE_RATE, play_rate)
                self.ui.push_audio_levels(_pcm_to_bands(chunk))
                try:
                    await asyncio.to_thread(stream.write, chunk)
                except Exception as e:
                    print(f"[NEO] ❌ Play write: {e}")
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
                    stream = None
                    sd.stop()
                    playback_ok = False
                    print("[NEO] Speaker stream lost — retrying on next reply.")
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

    async def _idle_mic_watch(self):
        """Close the mic after silence — wake window or post-reply follow-up."""
        while True:
            await asyncio.sleep(0.25)
            if not self._smart_mode or self.ui.manual_mute:
                continue
            phase = self._get_phase()
            if phase not in (MicPhase.USER_SPEAKING, MicPhase.FOLLOWUP):
                continue
            with self._speaking_lock:
                if self._is_speaking:
                    continue
            if self._processing or self._neo_streaming or self._turn_finalize_pending:
                continue
            if self.audio_in_queue and not self.audio_in_queue.empty():
                continue

            now = time.time()
            orb_idle = self._orb_hide_at > 0 and now >= self._orb_hide_at
            if orb_idle:
                self._go_standby()
            elif phase == MicPhase.FOLLOWUP and now >= self._followup_deadline:
                self._go_standby()
            elif (
                phase == MicPhase.USER_SPEAKING
                and self._listen_deadline > 0
                and now >= self._listen_deadline
            ):
                self._go_standby()

    def _report_connect_issue(self, kind: str, message: str, *, repeat: int) -> None:
        """Log + show status without spamming identical errors every 3 seconds."""
        if repeat == 0:
            print(f"[NEO] ⚠️ {message}")
            self._show_status_text(message)
        elif repeat % 6 == 0:
            print(f"[NEO] ⚠️ Still waiting ({kind})…")

    async def run(self):
        key_err = _api_key_config_error()
        if key_err:
            print(f"[NEO] ❌ {key_err}")
            self._show_status_text(key_err)
            return

        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"},
        )

        backoff = 3.0
        last_kind: str | None = None
        repeat = 0

        while True:
            if not _gemini_host_reachable():
                msg = _connect_error_message("network")
                if last_kind != "network":
                    repeat = 0
                self._report_connect_issue("network", msg, repeat=repeat)
                last_kind = "network"
                repeat += 1
                self.ui.set_state("THINKING")
                await asyncio.sleep(min(backoff, 20.0))
                backoff = min(backoff * 1.4, 60.0)
                continue

            try:
                print("[NEO] 🔌 Connecting...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=10)
                    self._turn_done_event = asyncio.Event()
                    self._tool_send_lock = asyncio.Lock()

                    backoff = 3.0
                    last_kind = None
                    repeat = 0

                    print(f"[NEO] Connected. Voice={_live_voice_name()} tools={len(CORE_TOOL_DECLARATIONS)}")
                    if self._smart_mode:
                        self._user_spoke_this_turn = False
                        self._set_phase(MicPhase.STANDBY)
                        print("[NEO] Standby — say 'Hey Neo' or clap twice.")
                    else:
                        self.ui.set_state("LISTENING")
                        print("[NEO] Online.")

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._idle_mic_watch())

            except asyncio.CancelledError:
                break
            except Exception as e:
                subs = (
                    list(e.exceptions)
                    if isinstance(e, BaseExceptionGroup)
                    else [e]
                )
                kinds: list[str] = []
                for sub in subs:
                    kind = _classify_connect_error(sub)
                    if kind == "cancelled":
                        continue
                    kinds.append(kind)
                    if kind == "transient" and repeat == 0:
                        print(f"[NEO] Session ended ({type(sub).__name__})")
                    elif kind == "unknown" and repeat == 0:
                        print(f"[NEO] ⚠️ {sub}")
                        traceback.print_exception(type(sub), sub, sub.__traceback__)

                if not kinds:
                    continue

                if "auth" in kinds:
                    kind = "auth"
                elif "network" in kinds:
                    kind = "network"
                elif "transient" in kinds:
                    kind = "transient"
                else:
                    kind = kinds[0]

                if kind != last_kind:
                    repeat = 0
                msg = _connect_error_message(kind, subs[0] if subs else None)
                if kind in ("auth", "network"):
                    self._report_connect_issue(kind, msg, repeat=repeat)
                elif kind == "transient" and repeat == 0:
                    self._show_status_text(msg)

                last_kind = kind
                repeat += 1
                if kind == "auth":
                    backoff = max(backoff, 30.0)
                elif kind == "network":
                    backoff = min(max(backoff, 5.0) * 1.4, 60.0)
                else:
                    backoff = min(backoff * 1.35, 30.0)
            finally:
                self._reset_live_session_state()
                self.set_speaking(False, block_wake_after=False)
                self.session = None
                self._tool_send_lock = None
                sd.stop()

            self.ui.set_state("THINKING")
            wait = int(backoff)
            if repeat <= 1 or repeat % 6 == 0:
                print(f"[NEO] 🔄 Reconnecting in {wait}s…")
            await asyncio.sleep(backoff)

def main():
    ui = NeoUI("face.png")

    # ── Startup validation ──
    try:
        from core.startup_check import run_all_checks
        report = run_all_checks(verbose=True)
        for line in report.to_lines():
            print(line)
        if not report.all_pass:
            print("[NEO] ⚠️ Some dependency checks failed — NEO may have limited functionality.")
    except Exception:
        pass

    try:
        from actions.vision_local import preload_vision_models

        preload_vision_models(background=True)
    except Exception:
        pass

    def runner():
        ui.wait_for_api_key()
        neo = NeoLive(ui)
        try:
            asyncio.run(neo.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()
