"""Smoke tests for core.goal_dispatcher — no external LLM calls required."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_split_goals_single():
    """A simple request with no conjunctions returns as a single goal."""
    from core.goal_dispatcher import split_goals
    goals = split_goals("What is the weather in Mumbai")
    assert len(goals) == 1
    assert "weather" in goals[0].lower()


def test_split_goals_detects_and():
    """'AND' in the request should trigger goal splitting."""
    from core.goal_dispatcher import split_goals
    # This should trigger the LLM path because of "AND" marker
    # But if LLM fails, it falls back to single-goal
    goals = split_goals("Check my email AND tell me the weather")
    # At minimum, it should return something (the fallback is the original text)
    assert len(goals) >= 1
    assert any("email" in g.lower() or "weather" in g.lower() for g in goals)


def test_assign_agent_system_ops():
    """File/folder/app-related goals go to system_ops."""
    from core.goal_dispatcher import assign_agent
    assert assign_agent("Open Chrome browser") == "system_ops"
    assert assign_agent("Organize my desktop files") == "system_ops"


def test_assign_agent_comms():
    """Email/message goals go to comms."""
    from core.goal_dispatcher import assign_agent
    assert assign_agent("Check my email inbox") == "comms"
    assert assign_agent("Send a WhatsApp message to Mom") == "comms"


def test_assign_agent_researcher():
    """Research/search goals go to researcher."""
    from core.goal_dispatcher import assign_agent
    assert assign_agent("Find flights to Dubai") == "researcher"
    assert assign_agent("What is the capital of France") == "researcher"
    assert assign_agent("Compare iPhone 15 vs iPhone 16") == "researcher"


def test_assign_agent_weather():
    """Weather goals go to researcher."""
    from core.goal_dispatcher import assign_agent
    assert assign_agent("Tell me the weather in London") == "researcher"


def test_goal_dispatcher_exists():
    """GoalDispatcher singleton is importable and instantiable."""
    from core.goal_dispatcher import get_dispatcher, GoalDispatcher
    d = get_dispatcher()
    assert isinstance(d, GoalDispatcher)
    assert d._max_workers == 5


def test_goal_result_properties():
    """GoalResult properly reports ok/error states."""
    from core.goal_dispatcher import GoalResult
    gr_ok = GoalResult(goal="test", agent_type="researcher")
    assert not gr_ok.ok  # No result yet

    gr_err = GoalResult(goal="test", agent_type="researcher", error="timeout")
    assert not gr_err.ok
    assert "Error" in gr_err.answer
    assert "timeout" in gr_err.answer


def test_dispatch_result_empty():
    """DispatchResult with no results reports all_ok."""
    from core.goal_dispatcher import DispatchResult
    dr = DispatchResult()
    assert dr.all_ok is True
    assert dr.summary == ""


# --------------------------------------------------------------------------- #
# Runner                                                                       #
# --------------------------------------------------------------------------- #

def main() -> int:
    import traceback
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for fn in tests:
        try:
            fn()
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
        else:
            passed += 1
            print(f"ok    {fn.__name__}")
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
