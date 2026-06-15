"""Central LLM access for ARIA using litellm.

This replaces the old hardcoded Gemini integration with litellm, allowing
the user to configure any API key (OpenAI, Anthropic, Gemini, Groq, Ollama)
by simply setting the model string (e.g. "gpt-4o", "gemini/gemini-2.5-flash", "ollama/llama3").
"""

from __future__ import annotations

import json as _json
import re as _re
from typing import Any, Sequence
from base64 import b64encode
from io import BytesIO

import litellm

# Provide a fallback default model. Can be overridden in config later.
DEFAULT_MODEL = "gemini/gemini-2.5-flash-lite"

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

def _format_messages(prompt: str | Sequence[Any], system: str | None, images: Sequence[Any] | None) -> list[dict]:
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
        return get_api_key("gemini_api_key", required=False)
    if model.startswith("gpt") or model.startswith("openai"):
        return get_api_key("openai_api_key", required=False)
    if model.startswith("claude"):
        return get_api_key("anthropic_api_key", required=False)
    # litellm will try to look for ENV vars if api_key is None, 
    # but we will just pass what we have from keyring.
    return None

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
    """Generate text using litellm."""
    
    # Prefix un-prefixed gemini models to ensure litellm routes them correctly
    if model.startswith("gemini-"):
        model = f"gemini/{model}"

    messages = _format_messages(prompt, system, images)
    kwargs = {
        "model": model,
        "messages": messages,
    }
    
    api_key = _get_api_key_for_model(model)
    if api_key:
        kwargs["api_key"] = api_key

    if temperature is not None:
        kwargs["temperature"] = temperature
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
        
    resp = litellm.completion(**kwargs)
    return (resp.choices[0].message.content or "").strip()

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
