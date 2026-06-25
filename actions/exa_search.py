"""exa_search --- Neural web search and content extraction via Exa API.

Canonical reference: https://docs.exa.ai/reference/search-api-guide-for-coding-agents

Search modes:
  - Search the web with neural relevance (auto / fast / instant / deep)
  - Get parsed content for URLs you already have (/contents)
  - Structured output with outputSchema for grounded JSON extraction
"""

from __future__ import annotations

from typing import Any


TOOL_DECLARATION = {
    "type": "function",
    "function": {
        "name": "exa_search",
        "description": "Search the web or extract content from URLs using Exa neural search. "
                       "Supports auto (balanced), fast (latency), instant (chat), "
                       "deep-lite/deep (thorough research). Returns highlights, text, or structured output.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "type": {
                    "type": "string",
                    "enum": ["auto", "fast", "instant", "deep-lite", "deep", "deep-reasoning"],
                    "description": "Search type. Default: auto."
                },
                "num_results": {"type": "integer", "description": "Number of results (1-50). Default: 10."},
                "content_mode": {
                    "type": "string",
                    "enum": ["highlights", "text", "summary", "none"],
                    "description": "Content to retrieve. Default: highlights."
                },
                "max_characters": {"type": "integer", "description": "Max chars per result when content_mode=text."},
                "urls": {"type": "string", "description": "Comma-separated URLs to fetch content from."},
                "include_domains": {"type": "string", "description": "Comma-separated domains to restrict to."},
                "exclude_domains": {"type": "string", "description": "Comma-separated domains to exclude."},
                "max_age_hours": {"type": "integer", "description": "Max cache age in hours. 0=livecrawl, -1=cache only."},
                "summary_query": {"type": "string", "description": "Bias summaries toward this question."}
            },
            "required": []
        }
    }
}


def _get_exa_client():
    from exa_py import Exa
    from config import get_api_key
    key = get_api_key("exa_api_key", required=True)
    return Exa(api_key=key)


def _format_results(results, mode: str) -> str:
    if not results or not hasattr(results, "results") or not results.results:
        return "No results found."
    if hasattr(results, "output") and results.output and results.output.content:
        import json
        structured = json.dumps(results.output.content, indent=2)
        grounding = ""
        if hasattr(results.output, "grounding") and results.output.grounding:
            g_lines = []
            for g in results.output.grounding:
                urls = ", ".join(c.get("url", "") for c in getattr(g, "citations", []))
                g_lines.append(f"  {getattr(g, 'field', '?')}: {urls}")
            if g_lines:
                grounding = "\nSources:\n" + "\n".join(g_lines)
        return f"Structured Output:\n{structured}{grounding}"
    lines = [f"Found {len(results.results)} result(s):\n"]
    for i, r in enumerate(results.results, 1):
        title = getattr(r, "title", "") or ""
        url = getattr(r, "url", "") or ""
        lines.append(f"{i}. {title}")
        lines.append(f"   {url}")
        highlights = getattr(r, "highlights", None)
        if highlights:
            for h in highlights[:3]:
                lines.append(f"   > {h[:200]}")
        summary = getattr(r, "summary", None)
        if summary:
            lines.append(f"   \U0001f4dd {summary[:300]}")
        text = getattr(r, "text", None)
        if text:
            txt = text[:300]
            if len(text) > 300:
                txt += "..."
            lines.append(f"   \U0001f4c4 {txt}")
        lines.append("")
    return "\n".join(lines)




def exa_search(parameters: dict | None = None, **kwargs) -> str:
    p = parameters or {}
    query = (p.get("query") or "").strip()
    urls_str = (p.get("urls") or "").strip()
    if not query and not urls_str:
        return "Please provide a search query or URLs to fetch."
    search_type = (p.get("type") or "auto").strip().lower()
    if search_type not in ("auto", "fast", "instant", "deep-lite", "deep", "deep-reasoning"):
        search_type = "auto"
    num_results = int(p.get("num_results") or 10)
    content_mode = (p.get("content_mode") or "highlights").strip().lower()
    max_chars = int(p.get("max_characters") or 5000)
    try:
        client = _get_exa_client()
        if urls_str:
            urls = [u.strip() for u in urls_str.split(",") if u.strip()]
            if not urls:
                return "No valid URLs provided."
            c_kwargs = _build_contents_kwargs(content_mode, max_chars, p.get("summary_query"))
            if content_mode == "none":
                c_kwargs = {}
            results = client.get_contents(urls, **c_kwargs)
            out = [f"Content for {len(urls)} URL(s):\n"]
            for r in (getattr(results, "results", None) or []):
                url = getattr(r, "url", "")
                title = getattr(r, "title", "") or "Untitled"
                out.append(f"  {title}")
                out.append(f"  {url}")
                highlights = getattr(r, "highlights", None)
                if highlights:
                    for h in highlights[:3]:
                        out.append(f"    > {h[:200]}")
                text = getattr(r, "text", None)
                if text:
                    txt = text[:500]
                    if len(text) > 500:
                        txt += "..."
                    out.append(f"    \U0001f4c4 {txt}")
                out.append("")
            return "\n".join(out)
        kw: dict[str, Any] = {
            "query": query, "type": search_type, "num_results": num_results,
        }
        contents = _build_contents_kwargs(content_mode, max_chars, p.get("summary_query"))
        if contents and content_mode != "none":
            kw["contents"] = contents
        inc_domains = (p.get("include_domains") or "").strip()
        if inc_domains:
            kw["include_domains"] = [d.strip() for d in inc_domains.split(",") if d.strip()]
        exc_domains = (p.get("exclude_domains") or "").strip()
        if exc_domains:
            kw["exclude_domains"] = [d.strip() for d in exc_domains.split(",") if d.strip()]

        max_age = p.get("max_age_hours")
        if max_age is not None:
            kw["max_age_hours"] = int(max_age)
        print(f"[ExaSearch] \U0001f50d {search_type} query={query!r} num={num_results}")
        results = client.search(**kw)
        return _format_results(results, content_mode)
    except ImportError:
        return "Exa library not installed. Run: pip install exa-py"
    except Exception as e:
        err = str(e)
        print(f"[ExaSearch] \u274c Error: {err}")
        if "401" in err or "unauthorized" in err.lower() or "api key" in err.lower():
            return "ExaSearch: Invalid or missing API key. Set EXA_API_KEY in .env."
        return f"ExaSearch failed: {err}"


def _build_contents_kwargs(mode: str, max_chars: int,
                           summary_query: str | None = None) -> dict:
    if mode == "highlights":
        return {"highlights": True}
    elif mode == "text":
        return {"text": {"max_characters": max_chars}}
    elif mode == "summary":
        sq = {}
        if summary_query:
            sq["query"] = summary_query
        if sq:
            return {"summary": sq}
        return {"summary": True}
    return {}
