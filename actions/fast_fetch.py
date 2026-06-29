"""
Fast fetch module for real-world APIs without headless browser overhead.
Uses Open-Meteo for Weather, Wikipedia REST API for facts, and DuckDuckGo for fast news/search.
"""

import requests
from urllib.parse import quote_plus
from duckduckgo_search import DDGS

def get_weather(location: str) -> str:
    """Fetch weather using Open-Meteo."""
    from actions.weather_report import fetch_weather

    return fetch_weather(location)


def get_wikipedia(query: str) -> str:
    """Fetch Wikipedia summary."""
    try:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote_plus(query.replace(' ', '_'))}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            return res.json().get("extract", "No summary available.")
        elif res.status_code == 404:
            # Fallback search
            search_url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={quote_plus(query)}&limit=1&namespace=0&format=json"
            s_res = requests.get(search_url, timeout=5).json()
            if len(s_res) > 1 and s_res[1]:
                return get_wikipedia(s_res[1][0])
            return f"No Wikipedia page found for '{query}'."
        return f"Wikipedia fetch failed with status {res.status_code}"
    except Exception as e:
        return f"Wikipedia fetch failed: {e}"


def get_news(topic: str = "") -> str:
    """Fetch latest news using DuckDuckGo Search."""
    try:
        ddgs = DDGS()
        query = topic if topic else "latest world news"
        results = list(ddgs.news(query, max_results=5))
        if not results:
            return f"No news found for '{query}'."
        
        out = f"News for '{query}':\n"
        for r in results:
            out += f"- {r.get('title')} ({r.get('source', 'Unknown')})\n  {r.get('url')}\n"
        return out.strip()
    except Exception as e:
        return f"News fetch failed: {e}"


def fast_fetch(parameters: dict, response=None, player=None, session_memory=None) -> str:
    """Main router for fast_fetch tool."""
    action = parameters.get("action", "").lower().strip()
    query = parameters.get("query", "").strip()

    if player:
        player.write_log(f"[FastFetch] {action}: {query}")

    if action == "weather":
        return get_weather(query)
    elif action == "wikipedia":
        return get_wikipedia(query)
    elif action == "news":
        return get_news(query)
    else:
        return f"Unknown action: {action}. Use weather, wikipedia, or news."
