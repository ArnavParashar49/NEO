import datetime

from main import _session_greeting


def test_session_greeting_is_time_aware_and_opt_in_for_news():
    morning = _session_greeting(datetime.datetime(2026, 7, 1, 9, 0))
    afternoon = _session_greeting(datetime.datetime(2026, 7, 1, 14, 0))
    evening = _session_greeting(datetime.datetime(2026, 7, 1, 20, 0))

    assert morning.startswith("Good morning. I'm doing well")
    assert afternoon.startswith("Good afternoon. I'm doing well")
    assert evening.startswith("Good evening. I'm doing well")
    assert "what are we working on today?" in morning
    assert "Want me to check" in morning
