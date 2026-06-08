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
