#web_search.py
import json
import re
import sys
import time
from pathlib import Path


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _get_api_key() -> str:
    from config import get_api_key
    return get_api_key()


def _ddgs_class():
    try:
        from ddgs import DDGS
        return DDGS
    except ImportError:
        from duckduckgo_search import DDGS
        return DDGS


def _gemini_search(query: str) -> str:
    from google import genai

    client = genai.Client(api_key=_get_api_key())
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=query,
        config={"tools": [{"google_search": {}}]},
    )

    text = ""
    for part in response.candidates[0].content.parts:
        if hasattr(part, "text") and part.text:
            text += part.text

    text = text.strip()
    if not text:
        raise ValueError("Gemini returned an empty response.")
    return text


def _gemini_search_with_retry(query: str, attempts: int = 3) -> str:
    last_err = None
    for i in range(attempts):
        try:
            return _gemini_search(query)
        except Exception as e:
            last_err = e
            err = str(e)
            if ("503" in err or "429" in err or "UNAVAILABLE" in err) and i < attempts - 1:
                wait = 1.5 * (i + 1)
                print(f"[WebSearch] ⏳ Retry in {wait:.1f}s ({e})")
                time.sleep(wait)
                continue
            raise
    raise last_err  # type: ignore[misc]


def _gemini_knowledge(query: str) -> str:
    """Fallback when live search is unavailable."""
    from google import genai

    client = genai.Client(api_key=_get_api_key())
    prompt = (
        f"{query}\n\n"
        "Give a helpful answer as a numbered list (1. 2. 3.) with specific "
        "product names, brands, and approximate prices in INR when relevant. "
        "Be concise and practical."
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    text = ""
    for part in response.candidates[0].content.parts:
        if hasattr(part, "text") and part.text:
            text += part.text
    text = text.strip()
    if not text:
        raise ValueError("Gemini knowledge fallback returned empty.")
    return text


def _ddg_search(query: str, max_results: int = 6) -> list[dict]:
    DDGS = _ddgs_class()
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title":   r.get("title",  ""),
                "snippet": r.get("body",   r.get("snippet", "")),
                "url":     r.get("href",   r.get("url", "")),
            })
    return results


def _format_ddg(query: str, results: list[dict]) -> str:
    if not results:
        return ""

    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        if r.get("title"):
            lines.append(f"{i}. {r['title']}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet']}")
        if r.get("url"):
            lines.append(f"   {r['url']}")
        lines.append("")
    return "\n".join(lines).strip()


def _compare(items: list[str], aspect: str) -> str:
    query = (
        f"Compare {', '.join(items)} in terms of {aspect}. "
        "Give specific facts and data as a numbered list."
    )
    try:
        return _gemini_search_with_retry(query)
    except Exception as e:
        print(f"[WebSearch] ⚠️ Gemini compare failed: {e}")

    all_results: dict[str, list] = {}
    for item in items:
        try:
            all_results[item] = _ddg_search(f"{item} {aspect}", max_results=3)
        except Exception:
            all_results[item] = []

    lines = [f"Comparison — {aspect.upper()}", "─" * 40]
    for item in items:
        lines.append(f"\n▸ {item}")
        for r in all_results.get(item, [])[:2]:
            if r.get("snippet"):
                lines.append(f"  • {r['snippet']}")
    return "\n".join(lines)


def web_search(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    query  = params.get("query", "").strip()
    mode   = params.get("mode",  "search").lower().strip()
    items  = params.get("items", [])
    aspect = params.get("aspect", "general").strip() or "general"

    if not query and not items:
        return "What should I search for?"

    if items and mode != "compare":
        mode = "compare"

    def _show_visuals(result: str):
        if player and result and hasattr(player, "show_visuals"):
            try:
                on_done = getattr(player, "_visuals_done_cb", None)
                vis_query = query or ", ".join(items)
                player.show_visuals(
                    result,
                    vis_query,
                    on_done=on_done,
                )
            except Exception as ve:
                print(f"[WebSearch] ⚠️ Visual feed: {ve}")

    print(f"[WebSearch] 🔍 Query: {query!r}  Mode: {mode}")

    try:
        if mode == "compare" and items:
            print(f"[WebSearch] 📊 Comparing: {items}")
            result = _compare(items, aspect)
            print("[WebSearch] ✅ Compare done.")
            _show_visuals(result)
            return result

        result = ""
        print("[WebSearch] 🌐 Trying Gemini search…")
        try:
            result = _gemini_search_with_retry(query)
            print("[WebSearch] ✅ Gemini search OK.")
        except Exception as e:
            print(f"[WebSearch] ⚠️ Gemini search failed: {e}")

        if not result:
            print("[WebSearch] 🦆 Trying DuckDuckGo…")
            try:
                ddg = _ddg_search(query)
                result = _format_ddg(query, ddg)
                print(f"[WebSearch] ✅ DDG: {len(ddg)} result(s).")
            except Exception as e:
                print(f"[WebSearch] ⚠️ DDG failed: {e}")

        if not result:
            print("[WebSearch] 🧠 Using knowledge fallback…")
            result = _gemini_knowledge(query)
            print("[WebSearch] ✅ Knowledge fallback OK.")

        _show_visuals(result)
        return result

    except Exception as e:
        print(f"[WebSearch] ❌ All backends failed: {e}")
        return f"Search failed, sir: {e}"
