"""Dependency-free smoke + unit tests for ARIA.

Run from the project root:

    python tests/test_smoke.py

Exits non-zero if anything fails. No pytest required (though the test_* funcs
are pytest-discoverable if you later add it).
"""

from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# --------------------------------------------------------------------------- #
# 1. Import smoke test — every action/hybrid module must import cleanly.       #
# --------------------------------------------------------------------------- #

def test_action_modules_import():
    failures = []
    for py in sorted((ROOT / "actions").glob("*.py")):
        if py.name == "__init__.py":
            continue
        mod = f"actions.{py.stem}"
        try:
            importlib.import_module(mod)
        except Exception as e:  # noqa: BLE001 - we want the full list
            failures.append(f"{mod}: {e.__class__.__name__}: {e}")
    assert not failures, "action modules failed to import:\n  " + "\n  ".join(failures)


def test_core_packages_import():
    for mod in ("config", "hybrid.registry", "hybrid.router", "hybrid.types",
                "actions.confirm_gate", "memory.memory_manager"):
        importlib.import_module(mod)


# --------------------------------------------------------------------------- #
# 2. Confirm-gate unit tests — the destructive-action authorization logic.     #
# --------------------------------------------------------------------------- #

def _fresh_gate():
    import actions.confirm_gate as cg
    cg.clear_pending()
    return cg


def test_confirm_requires_staging_then_matches():
    cg = _fresh_gate()
    # First call stages the op.
    msg = cg.needs_confirm("file_delete", "Delete x?", {"action": "delete", "name": "x"})
    assert msg.startswith("NEEDS_CONFIRM")
    # Matching confirm releases the staged params.
    proceed, stored, err = cg.consume_confirmed({"confirm": True}, "file_delete")
    assert proceed is True and err is None
    assert stored.get("name") == "x"


def test_confirm_for_wrong_action_is_rejected():
    cg = _fresh_gate()
    cg.needs_confirm("file_delete", "Delete x?", {"action": "delete", "name": "x"})
    # A confirm arriving from a *different* tool must NOT fire the staged delete.
    proceed, stored, err = cg.consume_confirmed({"confirm": True}, "merge_folders")
    assert proceed is False and stored is None and err is None
    # The original staged op is still intact for its own tool.
    proceed, stored, _ = cg.consume_confirmed({"confirm": True}, "file_delete")
    assert proceed is True and stored.get("name") == "x"


def test_no_confirm_does_not_proceed():
    cg = _fresh_gate()
    cg.needs_confirm("file_delete", "Delete x?", {"name": "x"})
    proceed, stored, err = cg.consume_confirmed({}, "file_delete")
    assert proceed is False and stored is None and err is None


def test_cancel_clears_pending():
    cg = _fresh_gate()
    cg.needs_confirm("file_delete", "Delete x?", {"name": "x"})
    proceed, stored, err = cg.consume_confirmed({"cancel": True}, "file_delete")
    assert proceed is False and err and err.startswith("CANCELLED")
    # Pending was cleared, so a later bare confirm has nothing staged.
    assert cg._pending is None


def test_as_bool():
    cg = _fresh_gate()
    assert cg.as_bool(True) is True
    assert cg.as_bool("yes") is True
    assert cg.as_bool("no") is False
    assert cg.as_bool(None, default=True) is True


# --------------------------------------------------------------------------- #
# 3. Autonomous agent loop — control flow, verified with a fake session.       #
# --------------------------------------------------------------------------- #

def _fresh_registry():
    from hybrid.registry import ToolRegistry
    reg = ToolRegistry()

    def echo(args, ctx):
        return f"echoed: {args.get('text', '')}"

    def danger(args, ctx):
        return "NEEDS_CONFIRM: really delete everything?"

    reg.register(name="echo", description="echo", parameters={}, handler=echo)
    reg.register(name="danger", description="danger", parameters={}, handler=danger)
    return reg


class _ScriptedSession:
    """Returns a pre-baked list of Turns, then a final text."""
    def __init__(self, turns, finalize_text="fake summary"):
        self._turns = list(turns)
        self._finalize_text = finalize_text
        self.results = []

    def step(self):
        from core.agent_loop import Turn
        return self._turns.pop(0) if self._turns else Turn(text="(out of script)")

    def add_tool_result(self, name, result):
        self.results.append((name, result))

    def finalize(self):
        return self._finalize_text


class _CountingSession:
    """Never finishes; emits a unique tool call each step (for budget tests)."""
    def __init__(self):
        self.n = 0

    def step(self):
        from core.agent_loop import Turn
        self.n += 1
        return Turn(calls=[("echo", {"i": self.n})])

    def add_tool_result(self, name, result):
        pass

    def finalize(self):
        return "summary after limit"


def test_agent_loop_runs_tool_then_answers():
    from core.agent_loop import Turn, run_agent
    reg = _fresh_registry()
    session = _ScriptedSession([
        Turn(calls=[("echo", {"text": "hi"})]),
        Turn(text="All done."),
    ])
    res = run_agent("say hi", registry=reg, session=session)
    assert res.stopped_reason == "done"
    assert res.answer == "All done."
    assert len(res.steps) == 1 and res.steps[0].tool == "echo"
    assert session.results == [("echo", "echoed: hi")]  # real result fed back


def test_agent_loop_stops_on_needs_confirm():
    from core.agent_loop import Turn, run_agent
    reg = _fresh_registry()
    session = _ScriptedSession([
        Turn(calls=[("danger", {})]),
        Turn(text="should never get here"),
    ])
    res = run_agent("delete stuff", registry=reg, session=session)
    assert res.stopped_reason == "needs_user"
    assert res.answer.startswith("NEEDS_CONFIRM")


def test_agent_loop_guards_against_thrash():
    from core.agent_loop import Turn, run_agent
    reg = _fresh_registry()
    same = Turn(calls=[("echo", {"text": "x"})])
    session = _ScriptedSession([same, same, same, same, same])
    res = run_agent("loop", registry=reg, session=session)
    assert res.stopped_reason == "loop_guard"


def test_agent_loop_respects_step_budget():
    from core.agent_loop import run_agent
    reg = _fresh_registry()
    res = run_agent("forever", registry=reg, session=_CountingSession(), max_steps=3)
    assert res.stopped_reason == "max_steps"
    assert len(res.steps) == 3
    assert res.answer == "summary after limit"


# --------------------------------------------------------------------------- #
# 4. Autonomous project builder — scoped tools, sandbox guard, confirm gate.   #
# --------------------------------------------------------------------------- #

def test_build_registry_is_scoped():
    """The build loop sees exactly the curated build tools — and dev_run is NOT
    exposed to the always-on global toolset."""
    from hybrid.bootstrap import register_all_tools
    from core.agent_loop import build_registry

    global_reg = register_all_tools()
    assert set(build_registry().names()) == {
        "file_controller", "web_search", "code_helper", "dev_run",
    }
    assert global_reg.lookup("dev_run") is None  # command runner stays scoped


def test_dev_run_refuses_outside_sandbox():
    from actions.dev_run import dev_run
    assert "refused" in dev_run({"command": "echo hi", "project_dir": "/etc"}).lower()


def test_project_builder_needs_description():
    from actions.project_builder import project_builder

    assert "describe" in project_builder({"action": "start", "description": ""}).lower()


def test_project_builder_start_builds_immediately():
    """action=start drives run_build (the autonomous loop) right away — no
    confirmation gate — and returns its answer. Verified with a fake loop."""
    import tempfile
    from pathlib import Path

    import core.agent_loop as al
    from actions import project_builder as pb
    from core.agent_loop import AgentResult

    seen: dict = {}

    def fake_run_build(goal, ctx=None, *, on_step=None, on_plan=None, max_steps=40):
        seen["goal"] = goal
        return AgentResult(
            answer="Built the snake game. Run: python main.py",
            steps=[], stopped_reason="done",
        )

    orig_run_build, orig_projects = al.run_build, pb.PROJECTS_DIR
    try:
        al.run_build = fake_run_build
        pb.PROJECTS_DIR = Path(tempfile.mkdtemp())
        out = pb.project_builder({"action": "start", "description": "snake game in python"})
    finally:
        al.run_build, pb.PROJECTS_DIR = orig_run_build, orig_projects

    assert "snake" in seen.get("goal", "").lower()
    assert "Built the snake game" in out


# --------------------------------------------------------------------------- #
# Runner                                                                       #
# --------------------------------------------------------------------------- #

def main() -> int:
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
