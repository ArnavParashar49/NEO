"""search_docs — Search documentation and API references for technologies.

Use when the user wants to:
- Find official documentation for a library/framework
- Look up API references
- Check best practices from official sources
- Find examples or patterns in docs
"""

from __future__ import annotations

from core.llm import ask
from core.models import RESEARCH


_SYSTEM_PROMPT = """You are NEO's documentation researcher. Given a technology and a question,
search for the answer in official documentation and reputable sources.

For each search, return:
1. A clear, direct answer to the question
2. The source URL(s)
3. Any relevant caveats or version-specific notes

Keep it concise. If you find conflicting information, note it.
If the documentation doesn't cover the question, say so honestly."""


def search_docs(parameters: dict | None = None, *, player=None, speak=None, **kwargs) -> str:
    """Search documentation for a technology topic.
    
    Args:
        parameters: dict with 'query' (what to search for) and optional
                   'technology' (specific library/framework to scope to)
    """
    p = parameters or {}
    query = (p.get("query") or "").strip()
    if not query:
        return "What documentation would you like me to search for?"

    technology = (p.get("technology") or "").strip()
    if technology:
        search_prompt = (
            f"Search for official documentation and best practices regarding: {query}\n"
            f"Technology scope: {technology}\n\n"
            f"Focus on official docs ({technology} docs, GitHub README, official site) "
            f"and reputable sources (MDN, dev.to, freeCodeCamp). "
            f"Provide the answer with source URLs."
        )
    else:
        search_prompt = (
            f"Search for official documentation regarding: {query}\n\n"
            f"Focus on official documentation and reputable sources. "
            f"Provide the answer with source URLs."
        )

    try:
        response = ask(search_prompt, model=RESEARCH, system=_SYSTEM_PROMPT, temperature=0.2)
        return response
    except Exception as e:
        return f"I couldn't search the documentation. Error: {e}"
