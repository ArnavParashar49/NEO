"""Multi-agent model routing for NEO.

Role → model mapping (override any via .env):

  Primary assistant     Gemini
  Research agent        Kimi (NVIDIA NIM)
  Fast utility          Gemini (Flash-Lite)
  Deep analysis         Claude Sonnet 4 (OpenRouter)
  File analysis         Gemini
  Vision / images       Gemini
  Voice (Live API)      Gemini native audio
  Fallbacks             OpenRouter → CometAPI

Note: Groq was removed from the default fast/deep roles because it produced
litellm provider errors during sub-agent dispatch. Set NEO_MODEL_FAST /
NEO_MODEL_DEEP in .env to re-enable Groq (or any other provider) if desired.
"""

from __future__ import annotations

import os

_GEMINI_FLASH = "gemini/gemini-2.5-flash"
_GEMINI_LITE = "gemini/gemini-2.5-flash-lite"
_KIMI_K2 = "kimi/moonshotai/kimi-k2.6"
# Kept for reference / optional .env overrides; no longer the default.
_GROQ_FAST = "groq/llama-3.1-8b-instant"
_GROQ_DEEP = "groq/llama-3.3-70b-versatile"


def _model(env_key: str, default: str) -> str:
    val = os.environ.get(env_key, "").strip()
    return val or default


# ── Agent roles ─────────────────────────────────────────────────────────────
PRIMARY = _model("NEO_MODEL_PRIMARY", _GEMINI_LITE)
RESEARCH = _model("NEO_MODEL_RESEARCH", _KIMI_K2)
FILE_ANALYSIS = _model("NEO_MODEL_FILE", _GEMINI_FLASH)
VISION = _model("NEO_MODEL_VISION", _GEMINI_FLASH)
FAST_UTILITY = _model("NEO_MODEL_FAST", _GEMINI_LITE)
DEEP_ANALYSIS = _model("NEO_MODEL_DEEP", "groq/llama-3.3-70b-versatile")

# Gemini Live voice session (google.genai model id, not litellm prefix)
VOICE_LIVE = _model(
    "NEO_MODEL_VOICE",
    "models/gemini-2.5-flash-native-audio-preview-12-2025",
)

# Grounded web search requires Gemini + google_search tool
WEB_SEARCH_GROUNDED = _model("NEO_MODEL_WEB_SEARCH", _GEMINI_FLASH)

# ── Fallback chain (Groq → CometAPI) ───────────────────────────────────
FALLBACK_GROQ = _model(
    "NEO_MODEL_FALLBACK_GROQ",
    "groq/llama-3.3-70b-versatile",
)
FALLBACK_COMETAPI = _model(
    "NEO_MODEL_FALLBACK_COMETAPI",
    "cometapi/gpt-4o",
)

FALLBACK_CHAIN: tuple[str, ...] = (
    PRIMARY,
    FALLBACK_GROQ,
    FALLBACK_COMETAPI,
)

# LiteLLM default when no model is specified
DEFAULT_MODEL = PRIMARY

# Strip gemini/ prefix for raw google.genai SDK calls
def gemini_sdk_model(litellm_model: str) -> str:
    m = litellm_model.strip()
    if m.startswith("gemini/"):
        return m.split("/", 1)[1]
    if m.startswith("models/"):
        return m
    return m
