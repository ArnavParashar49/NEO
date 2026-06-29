"""Last tool result context for follow-up commands (links, 'that flight', etc.)."""

from __future__ import annotations

import re
import threading
from typing import Any

_lock = threading.Lock()
_ctx: dict[str, Any] = {}

_LINK_INTENT_RE = re.compile(
    r"\b("
    r"link|url|open (?:it|that|the)|send (?:me )?(?:the )?link|"
    r"give (?:me )?(?:the )?link|show (?:me )?(?:the )?link|"
    r"take me there|go to (?:that|the|this)"
    r")\b",
    re.I,
)

_ROUTE_RE = re.compile(
    r"\b(?:from|between)\s+([a-z][a-z\s\-]+?)\s+(?:to|→)\s+([a-z][a-z\s\-]+?)\b",
    re.I,
)

_SITE_ALIASES: dict[str, str] = {
    "goibibo": "goibibo",
    "ixigo": "ixigo",
    "makemytrip": "makemytrip",
    "mmt": "makemytrip",
    "google": "google",
    "google flights": "google",
}


def _city_code(city: str) -> str:
    from actions.flight_finder import city_to_code

    return city_to_code(city)


def build_goibibo_url(origin: str, destination: str, date: str, passengers: int = 1) -> str:
    o = _city_code(origin)
    d = _city_code(destination)
    return (
        f"https://www.goibibo.com/flights/results/"
        f"?from={o}&to={d}&departure={date}&tripType=O&cabinClass=E"
        f"&pax=A-{passengers}_C-0_I-0"
    )


def build_ixigo_url(origin: str, destination: str, date: str, passengers: int = 1) -> str:
    o = _city_code(origin)
    d = _city_code(destination)
    return (
        f"https://www.ixigo.com/flights/search"
        f"?from={o}&to={d}&date={date}&adults={passengers}&class=e"
    )


def set_flight(
    *,
    origin: str,
    destination: str,
    date: str,
    page_url: str,
    cheapest_airline: str = "",
    cheapest_price: str = "",
    passengers: int = 1,
) -> None:
    links = {
        "google": page_url,
        "goibibo": build_goibibo_url(origin, destination, date, passengers),
        "ixigo": build_ixigo_url(origin, destination, date, passengers),
    }
    with _lock:
        _ctx.clear()
        _ctx.update(
            {
                "type": "flight",
                "origin": origin,
                "destination": destination,
                "date": date,
                "page_url": page_url,
                "cheapest_airline": cheapest_airline,
                "cheapest_price": cheapest_price,
                "booking_links": links,
            }
        )


def set_web_search(*, query: str, summary: str) -> None:
    route = _ROUTE_RE.search(query) or _ROUTE_RE.search(summary)
    links: dict[str, str] = {}
    origin = destination = date = ""
    if route:
        origin = route.group(1).strip()
        destination = route.group(2).strip()
        from actions.flight_finder import infer_date_from_text, city_to_code

        date = infer_date_from_text(query) or infer_date_from_text(summary)
        if date:
            links = {
                "goibibo": build_goibibo_url(origin, destination, date),
                "ixigo": build_ixigo_url(origin, destination, date),
            }
    with _lock:
        _ctx.clear()
        _ctx.update(
            {
                "type": "web_search",
                "query": query,
                "summary": (summary or "")[:800],
                "origin": origin,
                "destination": destination,
                "date": date,
                "booking_links": links,
            }
        )


def get() -> dict[str, Any]:
    with _lock:
        return dict(_ctx)


def resolve_link(user_text: str) -> str | None:
    text = (user_text or "").strip()
    if not text:
        return None

    ctx = get()
    if not ctx:
        return None

    lower = text.lower()
    links = ctx.get("booking_links") or {}

    for alias, key in _SITE_ALIASES.items():
        if alias in lower and key in links:
            return links[key]

    if ctx.get("type") == "flight":
        if _LINK_INTENT_RE.search(text):
            return ctx.get("page_url") or links.get("google")
        # "cheapest flight on goibibo" / "that goibibo one" without saying "link"
        if any(alias in lower for alias in _SITE_ALIASES):
            return links.get("goibibo") or ctx.get("page_url")

    if _LINK_INTENT_RE.search(text) and links:
        return next(iter(links.values()), None)

    return None


def format_for_prompt() -> str:
    ctx = get()
    if not ctx:
        return ""

    lines = ["[LAST ACTION CONTEXT — use for follow-ups like 'the link', 'open that', 'goibibo link']"]
    if ctx.get("type") == "flight":
        lines.append(
            f"Last flight search: {ctx.get('origin')} → {ctx.get('destination')} on {ctx.get('date')}."
        )
        if ctx.get("cheapest_airline"):
            lines.append(
                f"Cheapest mentioned: {ctx.get('cheapest_airline')} at {ctx.get('cheapest_price')}."
            )
        links = ctx.get("booking_links") or {}
        if links.get("google"):
            lines.append(f"Google Flights URL: {links['google']}")
        if links.get("goibibo"):
            lines.append(f"Goibibo search URL: {links['goibibo']}")
        if links.get("ixigo"):
            lines.append(f"Ixigo search URL: {links['ixigo']}")
        lines.append(
            "If user asks for 'the link', 'open it', or a site name — open the matching URL above, "
            "NOT the site homepage."
        )
    elif ctx.get("type") == "web_search":
        lines.append(f"Last web search: {ctx.get('query')}")
        if ctx.get("summary"):
            lines.append(f"Result summary: {ctx.get('summary')[:400]}")
        links = ctx.get("booking_links") or {}
        for site, url in links.items():
            lines.append(f"{site.title()} URL: {url}")
    lines.append("")
    return "\n".join(lines)


def open_message(url: str) -> str:
    ctx = get()
    if ctx.get("type") == "flight":
        return (
            f"Opened the flight search for {ctx.get('origin')} to {ctx.get('destination')} "
            f"on {ctx.get('date')}."
        )
    return f"Opened the link."
