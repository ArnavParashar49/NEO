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
    from core.models import WEB_SEARCH_GROUNDED, gemini_sdk_model

    client = genai.Client(api_key=_get_api_key())
    response = client.models.generate_content(
        model=gemini_sdk_model(WEB_SEARCH_GROUNDED),
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


def _research_synthesize(query: str, *, context: str = "") -> str:
    """Research-agent synthesis (Kimi)."""
    from core.llm import ask
    from core.models import RESEARCH

    body = context.strip()
    prompt = (
        f"Research query: {query}\n\n"
        f"{('Sources and context:\\n' + body + '\\n\\n') if body else ''}"
        "Give a helpful answer as a numbered list (1. 2. 3.) with specific "
        "product names, brands, and approximate prices in INR when relevant. "
        "Be concise and practical."
    )
    return ask(prompt, model=RESEARCH, temperature=0.3)


def _exa_search(query: str) -> str:
    """Search via Exa neural search engine. Returns formatted text or empty string on failure."""
    try:
        from exa_py import Exa
        from config import get_api_key

        key = get_api_key("exa_api_key", required=False)
        if not key:
            return ""

        client = Exa(api_key=key)
        results = client.search(
            query,
            type="auto",
            num_results=8,
            contents={"highlights": True},
        )
        if not results or not hasattr(results, "results") or not results.results:
            return ""

        lines = [f"Exa search results for: {query}\n"]
        for i, r in enumerate(results.results, 1):
            title = getattr(r, "title", "") or ""
            url = getattr(r, "url", "") or ""
            lines.append(f"{i}. {title}")
            lines.append(f"   {url}")
            highlights = getattr(r, "highlights", None)
            if highlights:
                for h in highlights[:3]:
                    lines.append(f"   > {h[:200]}")
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        print(f"[WebSearch] ⚠️ Exa search failed: {e}")
        return ""

def _gemini_knowledge(query: str) -> str:
    """Fallback when live search is unavailable — Kimi research agent."""
    return _research_synthesize(query)


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
    all_results: dict[str, list] = {}
    for item in items:
        try:
            all_results[item] = _ddg_search(f"{item} {aspect}", max_results=3)
        except Exception:
            all_results[item] = []

    if any(all_results.values()):
        lines = [f"Comparison — {aspect.upper()}", "─" * 40]
        for item in items:
            lines.append(f"\n▸ {item}")
            for r in all_results.get(item, [])[:2]:
                if r.get("snippet"):
                    lines.append(f"  • {r['snippet']}")
        return "\n".join(lines)

    query = (
        f"Compare {', '.join(items)} in terms of {aspect}. "
        "Give specific facts and data as a numbered list."
    )
    try:
        return _gemini_search_with_retry(query)
    except Exception as e:
        print(f"[WebSearch] ⚠️ Gemini compare failed: {e}")
        return f"Could not compare {', '.join(items)}."


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
        vis_query = query or ", ".join(items)
        if not vis_query:
            return
        try:
            from actions.browser_native import is_visual_product_query, open_google_images

            if not is_visual_product_query(vis_query) and not is_visual_product_query(result[:400]):
                return
            on_done = getattr(player, "_visuals_done_cb", None) if player else None
            print(f"[WebSearch] 🖼 Opening Google Images for {vis_query!r}")

            def run():
                try:
                    open_google_images(vis_query)
                except Exception as ve:
                    print(f"[WebSearch] ⚠️ Visual open: {ve}")
                finally:
                    if on_done:
                        try:
                            on_done()
                        except Exception as cb_err:
                            print(f"[WebSearch] ⚠️ Visual on_done: {cb_err}")

            import threading
            threading.Thread(target=run, daemon=True, name="ARIA-web-visuals").start()
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

        print("[WebSearch] Trying Exa neural search...")
        try:
            exa_raw = _exa_search(query)
            if exa_raw:
                print("[WebSearch] Exa search OK.")
                _show_visuals(exa_raw)
                return exa_raw
        except Exception as e:
            print(f"[WebSearch] Exa failed: {e}")
        result = ""
        print("[WebSearch] 🦆 Trying DuckDuckGo…")
        try:
            ddg = _ddg_search(query)
            raw = _format_ddg(query, ddg)
            print(f"[WebSearch] ✅ DDG: {len(ddg)} result(s).")
            if raw:
                # Skip Kimi synthesis for short factual queries — saves 4–8s
                _needs_synthesis = len(query.split()) > 4 or any(
                    kw in query.lower()
                    for kw in ("compare", "best", "review", "vs", "why", "how",
                               "explain", "analyze", "discuss", "trade", "vs.")
                )
                if _needs_synthesis:
                    try:
                        print("[WebSearch] 🧠 Kimi research synthesis…")
                        result = _research_synthesize(query, context=raw)
                        print("[WebSearch] ✅ Kimi synthesis OK.")
                    except Exception as ke:
                        print(f"[WebSearch] ⚠️ Kimi synthesis failed: {ke}")
                        result = raw
                else:
                    # Short factual — DDG snippets are enough
                    result = raw
                    print("[WebSearch] ⚡ Skipped Kimi synthesis (short query)")
        except Exception as e:
            print(f"[WebSearch] ⚠️ DDG failed: {e}")

        if not result:
            print("[WebSearch] 🌐 Trying Gemini search…")
            try:
                result = _gemini_search_with_retry(query)
                print("[WebSearch] ✅ Gemini search OK.")
            except Exception as e:
                print(f"[WebSearch] ⚠️ Gemini search failed: {e}")

        if not result:
            print("[WebSearch] 🧠 Using knowledge fallback…")
            result = _gemini_knowledge(query)
            print("[WebSearch] ✅ Knowledge fallback OK.")

        _show_visuals(result)
        return result

    except Exception as e:
        print(f"[WebSearch] ❌ All backends failed: {e}")
        return f"Search failed, sir: {e}"
