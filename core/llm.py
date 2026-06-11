"""Central Gemini access for ARIA.

Every module used to repeat the same boilerplate — import the SDK, configure the
API key, build a model, call ``generate_content``, read ``.text`` — against the
*deprecated* ``google.generativeai`` package. This consolidates all of that into
one place built on the current ``google.genai`` SDK:

    from core.llm import ask, ask_json

    text = ask("Summarize this", model="gemini-2.5-flash")
    data = ask_json("Return JSON ...")
    desc = ask("What is this?", images=[pil_image])

A single ``Client`` is reused for the process. Callers keep passing whatever
model string they used before, so behavior is unchanged aside from the SDK.
"""

from __future__ import annotations

import json as _json
import re as _re
from functools import lru_cache
from typing import Any, Sequence

from google import genai
from google.genai import types

# Sensible default; nearly every call site passes its own model explicitly.
DEFAULT_MODEL = "gemini-2.5-flash-lite"


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    from config import get_api_key

    return genai.Client(api_key=get_api_key())


def _config(
    *,
    system: str | None,
    temperature: float | None,
    json_mode: bool,
    thinking_budget: int | None = None,
) -> types.GenerateContentConfig | None:
    kwargs: dict[str, Any] = {}
    if system:
        kwargs["system_instruction"] = system
    if temperature is not None:
        kwargs["temperature"] = temperature
    if json_mode:
        kwargs["response_mime_type"] = "application/json"
    if thinking_budget is not None:
        # Free-tier 2.5 models reason far better with thinking on; -1 lets the
        # model size its own budget, a positive int caps it.
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking_budget)
    return types.GenerateContentConfig(**kwargs) if kwargs else None


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
    """Generate text. Returns the response text (stripped), or "" if empty.

    ``prompt`` may be a string or a list of parts (e.g. strings + PIL images).
    ``images`` is a convenience: appended to the prompt as additional parts.
    ``thinking_budget`` enables Gemini 2.5 "thinking" (-1 = dynamic, N = token cap).
    """
    if isinstance(prompt, str):
        contents: list[Any] = [prompt]
    else:
        contents = list(prompt)
    if images:
        contents.extend(images)

    resp = _client().models.generate_content(
        model=model,
        contents=contents if len(contents) > 1 else contents[0],
        config=_config(
            system=system,
            temperature=temperature,
            json_mode=json_mode,
            thinking_budget=thinking_budget,
        ),
    )
    return (resp.text or "").strip()


def ask_json(
    prompt: str | Sequence[Any],
    *,
    model: str = DEFAULT_MODEL,
    system: str | None = None,
    temperature: float | None = None,
    images: Sequence[Any] | None = None,
) -> Any:
    """Like :func:`ask` but requests JSON and parses it (tolerates ``` fences)."""
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
