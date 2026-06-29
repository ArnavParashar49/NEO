"""Central LLM access for NEO using litellm.

Includes a dynamic ProviderRouter that tracks provider health and builds
smart fallback chains instead of the old static FALLBACK_CHAIN.
"""

from __future__ import annotations

import json as _json
import logging
import os
import re as _re
import threading
import time as _time
from base64 import b64encode
from dataclasses import dataclass as _dataclass
from io import BytesIO
from typing import Any, Sequence

import litellm

litellm.suppress_debug_info = True

from core.models import DEFAULT_MODEL, FALLBACK_CHAIN

logger = logging.getLogger(__name__)


_COMETAPI_BASE = os.environ.get("COMETAPI_BASE_URL", "https://api.cometapi.com/v1")
_NVIDIA_NIM_BASE = os.environ.get("NVIDIA_NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")

# Maps nim/ model IDs → env var name for their dedicated API key
_NIM_MODEL_KEY_MAP: dict[str, str] = {
    "minimaxai/minimax-m3":              "NIM_MINIMAX_M3_KEY",
    "nvidia/nemotron-3-ultra-550b-a55b": "NIM_NEMOTRON_ULTRA_KEY",
    "stepfun-ai/step-3.7-flash":         "NIM_STEP37_FLASH_KEY",
    "z-ai/glm-5.1":                      "NIM_GLM51_KEY",
    "deepseek-ai/deepseek-v4-flash":     "NIM_DEEPSEEK_V4_FLASH_KEY",
    "deepseek-ai/deepseek-v4-pro":       "NIM_DEEPSEEK_V4_PRO_KEY",
    "google/gemma-4-31b-it":             "NIM_GEMMA4_31B_KEY",
    "qwen/qwen3.5-122b-a10b":            "NIM_QWEN35_122B_KEY",
    "minimaxai/minimax-m2.7":            "NIM_MINIMAX_M27_KEY",
    # General NIM models fall back to the main key
    "meta/llama-3.3-70b-instruct":       "NVIDIA_NIM_API_KEY",
}


def _pil_to_base64(img: Any) -> str:
    """Convert PIL image to base64 data URI."""
    try:
        from PIL import Image

        if isinstance(img, Image.Image):
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            buf = BytesIO()
            img.save(buf, format="JPEG")
            b64 = b64encode(buf.getvalue()).decode("utf-8")
            return f"data:image/jpeg;base64,{b64}"
    except ImportError:
        pass
    return ""


def _format_messages(
    prompt: str | Sequence[Any],
    system: str | None,
    images: Sequence[Any] | None,
) -> list[dict]:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})

    content_array = []

    if isinstance(prompt, str):
        content_array.append({"type": "text", "text": prompt})
    else:
        for part in prompt:
            if isinstance(part, str):
                content_array.append({"type": "text", "text": part})
            else:
                b64 = _pil_to_base64(part)
                if b64:
                    content_array.append({"type": "image_url", "image_url": {"url": b64}})

    if images:
        for img in images:
            b64 = _pil_to_base64(img)
            if b64:
                content_array.append({"type": "image_url", "image_url": {"url": b64}})

    messages.append({"role": "user", "content": content_array})
    return messages


def _get_api_key_for_model(model: str) -> str | None:
    from config import get_api_key

    if model.startswith("gemini"):
        return get_api_key("gemini_api_key", required=False) or None
    if model.startswith("gpt") or model.startswith("openai"):
        return get_api_key("openai_api_key", required=False) or None
    if model.startswith("claude"):
        return get_api_key("anthropic_api_key", required=False) or None
    if model.startswith("groq"):
        return get_api_key("groq_api_key", required=False) or None
    return None


def _route_provider(model: str) -> tuple[str, str | None, str | None]:
    """Return (litellm_model, api_key, api_base) after provider routing."""
    from config import get_api_key

    if model.startswith("cometapi/"):
        routed = model.replace("cometapi/", "openai/", 1)
        key = get_api_key("cometapi_api_key", required=False) or None
        return routed, key, _COMETAPI_BASE

    if model.startswith("nim/"):
        # nim/ prefix: look up per-model dedicated key, fall back to main NIM key
        actual_model = model[4:]  # strip 'nim/'
        env_var = _NIM_MODEL_KEY_MAP.get(actual_model, "NVIDIA_NIM_API_KEY")
        key = os.environ.get(env_var) or get_api_key("nvidia_nim_api_key", required=False) or None
        return f"openai/{actual_model}", key, _NVIDIA_NIM_BASE

    if model.startswith("nvidia/"):
        # nvidia/ prefix routes via NVIDIA NIM (primary key or per-model key)
        actual_model = model.replace("nvidia/", "", 1)
        env_var = _NIM_MODEL_KEY_MAP.get(f"nvidia/{actual_model}", "NVIDIA_NIM_API_KEY")
        key = os.environ.get(env_var) or get_api_key("nvidia_nim_api_key", required=False) or None
        return f"openai/{actual_model}", key, _NVIDIA_NIM_BASE

    if model.startswith("deepseek/"):
        actual_model = model.replace("deepseek/", "", 1)
        key = get_api_key("nvidia_nim_api_key", required=False) or None
        return f"openai/{actual_model}", key, _NVIDIA_NIM_BASE

    if model.startswith("kimi/"):
        actual_model = model.replace("kimi/", "", 1)
        key = (
            get_api_key("nvidia_nim_kimi_api_key", required=False)
            or get_api_key("nvidia_nim_api_key", required=False)
            or None
        )
        return f"openai/{actual_model}", key, _NVIDIA_NIM_BASE

    if model.startswith("openrouter/"):
        key = get_api_key("openrouter_api_key", required=False) or None
        return model, key, None

    return model, _get_api_key_for_model(model), None


# --------------------------------------------------------------------------- #
# Dynamic Provider Router                                                     #
# --------------------------------------------------------------------------- #

@_dataclass
class ProviderHealth:
    name: str
    last_check: float = 0.0
    healthy: bool = True
    failure_count: int = 0
    avg_latency_ms: float = 0.0


class ProviderRouter:
    """Routes model requests to the best available provider.

    - Health checks: marks providers unhealthy after 3 consecutive failures
    - Latency tracking: exponential moving average per provider
    - Dynamic fallback: skips unhealthy providers automatically
    - Auto-recovery: unhealthy providers retried after 60s cooldown
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._providers: dict[str, ProviderHealth] = {}
        self._cooldown_seconds = 60.0

    def _get(self, name: str) -> ProviderHealth:
        with self._lock:
            if name not in self._providers:
                self._providers[name] = ProviderHealth(name=name)
            return self._providers[name]

    def record_success(self, provider_key: str, latency_ms: float) -> None:
        p = self._get(provider_key)
        with self._lock:
            p.healthy = True
            p.failure_count = 0
            p.last_check = _time.time()
            if p.avg_latency_ms == 0:
                p.avg_latency_ms = latency_ms
            else:
                p.avg_latency_ms = 0.7 * p.avg_latency_ms + 0.3 * latency_ms

    def record_failure(self, provider_key: str) -> None:
        p = self._get(provider_key)
        with self._lock:
            p.failure_count += 1
            p.last_check = _time.time()
            if p.failure_count >= 3:
                p.healthy = False
                logger.warning(
                    "Provider %s unhealthy after %d failures",
                    provider_key, p.failure_count,
                )

    def is_healthy(self, provider_key: str) -> bool:
        p = self._get(provider_key)
        with self._lock:
            if not p.healthy:
                if _time.time() - p.last_check > self._cooldown_seconds:
                    p.healthy = True
                    p.failure_count = 0
                    logger.info("Provider %s auto-recovered", provider_key)
                    return True
                return False
            return True

    def get_fallback_chain(self, primary_model: str) -> list[str]:
        chain = [primary_model]
        if primary_model.startswith("gemini/"):
            chain.extend([
                "groq/llama-3.3-70b-versatile",
                "nim/deepseek-ai/deepseek-v4-flash",   # fast, free
                "nim/stepfun-ai/step-3.7-flash",       # fast, free
                "nvidia/meta/llama-3.3-70b-instruct",  # reliable
                "nim/qwen/qwen3.5-122b-a10b",          # large context
                "nim/deepseek-ai/deepseek-v4-pro",     # strong reasoning
                "nim/nvidia/nemotron-3-ultra-550b-a55b",# biggest
                "cometapi/gpt-4o",
            ])
        elif primary_model.startswith("groq/"):
            chain.extend([
                "gemini/gemini-2.5-flash-lite",
                "nim/deepseek-ai/deepseek-v4-flash",
                "nim/stepfun-ai/step-3.7-flash",
                "nvidia/meta/llama-3.3-70b-instruct",
                "nim/qwen/qwen3.5-122b-a10b",
                "cometapi/gpt-4o",
            ])
        elif primary_model.startswith("nim/") or primary_model.startswith("nvidia/"):
            chain.extend([
                "groq/llama-3.3-70b-versatile",
                "gemini/gemini-2.5-flash-lite",
                "nim/deepseek-ai/deepseek-v4-flash",
                "cometapi/gpt-4o",
            ])
        else:
            chain.extend([
                "gemini/gemini-2.5-flash-lite",
                "groq/llama-3.3-70b-versatile",
                "nim/deepseek-ai/deepseek-v4-flash",
                "nim/stepfun-ai/step-3.7-flash",
                "nvidia/meta/llama-3.3-70b-instruct",
                "nim/qwen/qwen3.5-122b-a10b",
                "nim/deepseek-ai/deepseek-v4-pro",
                "nim/nvidia/nemotron-3-ultra-550b-a55b",
                "cometapi/gpt-4o",
            ])
        healthy = [m for m in chain if self._is_model_healthy(m)]
        return healthy if healthy else chain

    def _is_model_healthy(self, model: str) -> bool:
        # nim/ prefix — each model has its own key so track per-model health
        if model.startswith("nim/"):
            model_id = model[4:]
            health_key = f"nim_{model_id.replace('/', '_').replace('.', '_').replace('-', '_')}"
            return self.is_healthy(health_key)
        for prefix, key in [
            ("gemini", "gemini"), ("groq", "groq"),
            ("cometapi", "cometapi"), ("gpt", "cometapi"),
            ("nvidia", "nvidia_nim"),
            ("deepseek", "nvidia_nim"), ("kimi", "nvidia_nim_kimi"),
            ("openrouter", "openrouter"),
        ]:
            if prefix in model:
                return self.is_healthy(key)
        return True


_provider_router: ProviderRouter | None = None


def get_provider_router() -> ProviderRouter:
    global _provider_router
    if _provider_router is None:
        _provider_router = ProviderRouter()
    return _provider_router


# --------------------------------------------------------------------------- #
# Main API                                                                     #
# --------------------------------------------------------------------------- #

def ask(
    prompt: str | Sequence[Any],
    *,
    model: str = DEFAULT_MODEL,
    system: str | None = None,
    temperature: float | None = None,
    images: Sequence[Any] | None = None,
    json_mode: bool = False,
    thinking_budget: int | None = None,
) -> str:
    """Generate text using litellm with provider routing."""
    if model.startswith("gemini-"):
        model = f"gemini/{model}"

    messages = _format_messages(prompt, system, images)
    model, api_key, api_base = _route_provider(model)

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }

    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["api_base"] = api_base
    if temperature is not None:
        kwargs["temperature"] = temperature
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if thinking_budget is not None and model.startswith("gemini/"):
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}

    start = _time.time()
    resp = litellm.completion(**kwargs)
    latency = (_time.time() - start) * 1000

    # Track provider health
    try:
        router = get_provider_router()
        if "gemini" in model:
            router.record_success("gemini", latency)
        elif "groq" in model:
            router.record_success("groq", latency)
        elif "nvidia" in model or "llama" in model or "mistral" in model:
            router.record_success("nvidia_nim", latency)
        elif "cometapi" in model or "openai" in model:
            router.record_success("cometapi", latency)
    except Exception:
        pass

    return (resp.choices[0].message.content or "").strip()


def ask_with_fallback(
    prompt: str | Sequence[Any],
    *,
    models: Sequence[str] | None = None,
    task: str | None = None,
    **kwargs: Any,
) -> str:
    """Try models in smart-pool order (best for task first).

    If explicit *models* list is given, use that chain instead of the pool.
    Pass task= hint ('fast','reasoning','coding','creative','vision') to
    let the pool pick the most suitable model automatically.
    """
    if not models:
        # Use SmartModelPool — picks best healthy model for the task
        from core.model_pool import pool_ask
        return pool_ask(prompt, task=task, **kwargs)

    # Explicit chain provided — iterate in order (legacy / tool-specific path)
    last_err: Exception | None = None
    for model in models:
        try:
            return ask(prompt, model=model, **kwargs)
        except Exception as e:
            last_err = e
            logger.warning("%s failed: %s", model, e)
            try:
                router = get_provider_router()
                if "gemini" in model:
                    router.record_failure("gemini")
                elif "groq" in model:
                    router.record_failure("groq")
                elif "nvidia" in model or "llama" in model or "mistral" in model:
                    router.record_failure("nvidia_nim")
                elif "cometapi" in model or "comet" in model:
                    router.record_failure("cometapi")
            except Exception:
                pass
    if last_err:
        raise last_err
    raise RuntimeError("No models configured for fallback chain")


def ask_json(
    prompt: str | Sequence[Any],
    *,
    model: str = DEFAULT_MODEL,
    system: str | None = None,
    temperature: float | None = None,
    images: Sequence[Any] | None = None,
) -> Any:
    """Like ask but requests JSON and parses it."""
    raw = ask(
        prompt,
        model=model,
        system=system,
        temperature=temperature,
        images=images,
        json_mode=True,
    )
    return _parse_json(raw)


def _parse_json(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = _re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", raw).strip()
    return _json.loads(raw)
