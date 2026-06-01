"""Speak weather aloud — never opens a browser."""


def weather_action(
    parameters: dict,
    player=None,
    session_memory=None,
) -> str:
    city = parameters.get("city")
    when = parameters.get("time", "today")

    if not city or not isinstance(city, str) or not city.strip():
        msg = "Which city should I check the weather for?"
        _log(msg, player)
        return msg

    city = city.strip()
    when = (when or "today").strip()

    query = (
        f"Weather in {city} {when}. "
        "Give current conditions, temperature, and today's high/low in 2–3 short "
        "sentences suitable for speaking aloud — warm, affirmative, a touch of dry humor if natural. "
        "Use Celsius unless the city is in the US."
    )

    try:
        from actions.web_search import (
            _ddg_search,
            _format_ddg,
            _gemini_knowledge,
            _gemini_search_with_retry,
        )

        msg = ""
        try:
            msg = _gemini_search_with_retry(query)
        except Exception as e:
            print(f"[Weather] ⚠️ Gemini search failed: {e}")

        if not msg:
            try:
                ddg = _ddg_search(f"weather {city} {when}")
                msg = _format_ddg(query, ddg)
            except Exception as e:
                print(f"[Weather] ⚠️ DDG failed: {e}")

        if not msg:
            msg = _gemini_knowledge(query)

        msg = (msg or "").strip()
        if not msg:
            raise ValueError("empty weather response")

    except Exception as e:
        msg = f"I couldn't get the weather for {city} right now."
        print(f"[Weather] ❌ {e}")

    _log(msg, player)

    if session_memory:
        try:
            session_memory.set_last_search(query=query, response=msg)
        except Exception:
            pass

    return msg


def _log(message: str, player=None) -> None:
    print(f"[Weather] {message}")
    if player:
        try:
            player.write_log(f"ARIA: {message}")
        except Exception:
            pass
