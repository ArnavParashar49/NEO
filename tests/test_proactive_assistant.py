from datetime import datetime, timedelta

from core import proactive_assistant as proactive


def setup_function():
    with proactive._pending_lock:
        proactive._pending = None


def test_date_answer_offers_reminder_and_yes_returns_concrete_action():
    suggestion = proactive.analyze_turn(
        "When is the product launch?", "The product launch is tomorrow."
    )

    assert suggestion == "Want me to remind you tomorrow at 9:00 AM?"
    kind, params = proactive.consume_reply("yes")
    assert kind == "reminder"
    assert params["date"] == (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    assert params["time"] == "09:00"


def test_no_cancels_pending_suggestion():
    proactive.analyze_turn("When is the meeting?", "It is tomorrow.")

    assert proactive.consume_reply("no") == ("cancelled", {})
    assert proactive.consume_reply("yes") is None


def test_unrelated_yes_is_not_consumed():
    assert proactive.consume_reply("yes") is None
    assert proactive.analyze_turn("Tell me a joke", "Tomorrow walks into a bar.") is None


def test_work_with_tomorrow_gets_reminder_offer():
    assert proactive.analyze_turn(
        "My work submission is tomorrow", "Understood."
    ) == "Want me to remind you tomorrow at 9:00 AM?"


def test_news_is_only_staged_until_user_approves():
    question = proactive.stage_news_offer()

    assert question == "Want me to check for any genuinely important updates too?"
    assert proactive.consume_reply("yes") == ("news", {})
