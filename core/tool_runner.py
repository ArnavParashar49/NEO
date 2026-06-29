"""Tool execution — fast path, hybrid orchestrator, circuit breaker integration.

Extracted from the 2200-line ``main.py`` to make tool dispatch independently testable.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import traceback

from google.genai import types

logger = logging.getLogger(__name__)

_HYBRID_ORCHESTRATOR = None
_SLOW_TOOLS = None


def _get_orchestrator():
    global _HYBRID_ORCHESTRATOR, _SLOW_TOOLS
    if _HYBRID_ORCHESTRATOR is None:
        from hybrid.bootstrap import init_hybrid_system
        _HYBRID_ORCHESTRATOR = init_hybrid_system()
        _SLOW_TOOLS = _HYBRID_ORCHESTRATOR.registry.slow_tools()
    return _HYBRID_ORCHESTRATOR


def slow_tools() -> set:
    _get_orchestrator()
    return _SLOW_TOOLS


# ── Fast-path cache ─────────────────────────────────────────────────────


class FastPathCache:
    """Deduplicate identical tool calls within a 15-second window."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._guard: dict | None = None

    def get(self, tool_name: str, args: dict) -> str | None:
        key = _args_key(tool_name, args)
        with self._lock:
            g = self._guard
            if not g or time.time() > g.get("until", 0):
                return None
            if g.get("key") == key:
                return g.get("result", "Done.")
        return None

    def register(self, tool_name: str, args: dict, result: str) -> None:
        key = _args_key(tool_name, args)
        with self._lock:
            self._guard = {
                "key": key,
                "tool": tool_name,
                "until": time.time() + 15.0,
                "result": result,
            }


def _args_key(tool_name: str, args: dict) -> str:
    import json
    try:
        return f"{tool_name}:{json.dumps(args or {}, sort_keys=True, default=str)}"
    except Exception:
        return f"{tool_name}:{args!r}"


# ── Tool filler phrases ─────────────────────────────────────────────────


def tool_status_line(name: str, args: dict) -> str:
    return _tool_filler_phrase(name, args)


def _tool_filler_phrase(name: str, args: dict) -> str:
    import random

    # Per-tool parameter-aware phrases
    if name == "web_search":
        q = (args.get("query") or "").strip()
        return f"On it — looking up {q[:50]}." if q else "Hunting that down for you."
    if name == "weather_report":
        city = (args.get("city") or "").strip()
        return f"Checking {city}'s weather..." if city else "Let me check the weather..."
    if name == "flight_finder":
        origin = (args.get("from") or args.get("origin") or "").strip()
        dest = (args.get("to") or args.get("destination") or "").strip()
        if origin and dest:
            return f"Hunting flights from {origin} to {dest}..."
        return "Hunting flights..."
    if name == "browser_control":
        url = (args.get("url") or "").strip()
        return f"Opening {url[:40]}..." if url else "Browser time..."
    if name == "discuss_project":
        topic = (args.get("topic") or args.get("question") or "").strip()
        return f"Thinking through {topic[:50]}..." if topic else "Let me think about that..."
    if name == "search_docs":
        q = (args.get("query") or "").strip()
        return f"Searching docs for {q[:40]}..." if q else "Checking the documentation..."
    if name == "send_email":
        subj = (args.get("subject") or "").strip()
        return f"Drafting email: {subj[:40]}..." if subj else "Drafting that email..."
    if name == "file_processor":
        return "Processing your file..."
    if name == "agent_task":
        g = (args.get("goal") or "")[:50]
        return f"Working on {g}..." if g else "Breaking that down..."

    generic = ("On it — back in a sec.", "Working on that now.", "Got it, let me handle this.")
    return random.choice(generic)


def tool_progress_eta(name: str) -> int:
    return {
        "web_search": 18, "youtube_video": 12, "flight_finder": 20,
        "file_processor": 15, "agent_task": 25,
        "weather_report": 4, "discuss_project": 18, "search_docs": 15,
    }.get(name, 12)


# ── Main tool dispatch ──────────────────────────────────────────────────


def run_local_system_control(user_text: str, *, last_system_command: str | None = None) -> tuple[bool, str]:
    """Execute volume/brightness locally without involving Gemini."""
    import re
    from actions.system_control import resolve_command_from_text, system_control

    _SYSTEM_CONTROL_RE = re.compile(
        r"\b(volume|brightness|brighter|dimmer|dim|louder|quieter|mute|loud)\b|"
        r"increase\s+(?:the\s+)?(?:screen\s+)?brightness|"
        r"turn\s+(?:up|down)\s+(?:the\s+)?volume",
        re.IGNORECASE,
    )
    text = user_text.strip()
    if not text or not _SYSTEM_CONTROL_RE.search(text):
        return False, ""

    cmd = resolve_command_from_text(text, last_system_command)
    if not cmd:
        return False, ""

    steps = 2 if any(p in text.lower() for p in ("more", "again", "even", "further", "keep")) else 1
    result = system_control({"command": cmd, "steps": steps, "last_command": last_system_command or ""})
    return True, result


async def handle_tool_calls(tool_call, *, session_mgr, audio) -> None:
    """Run tools off the receive loop so the live WebSocket stays alive."""
    fn_responses: list[types.FunctionResponse] = []
    try:
        for fc in tool_call.function_calls:
            response = await _execute_tool(fc, session_mgr=session_mgr, audio=audio)
            fn_responses.append(response)
        if session_mgr.session:
            await session_mgr.session.send_tool_response(
                function_responses=fn_responses,
            )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("Tool batch failed: %s", e)
        session_mgr.reset_live_session_state()


async def _execute_tool(fc, *, session_mgr, audio) -> types.FunctionResponse:
    """Execute a single tool function call."""
    name = fc.name
    args = dict(fc.args or {})

    _fast_cache = FastPathCache()
    dup = _fast_cache.get(name, args)
    if dup is not None:
        logger.info("Skipping duplicate %s (fast path)", name)
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": dup, "fast_path": True},
        )

    logger.info("Tool: %s %s", name, args)
    if hasattr(session_mgr, "ui") and session_mgr.ui:
        session_mgr.ui.set_state("THINKING")
    audio._ack_spoken_this_turn = False

    show_progress = name in slow_tools()
    if show_progress and not session_mgr._processing:
        label = tool_status_line(name, args)
        session_mgr.enter_processing(tool_progress_eta(name), label=label)

    if name == "shutdown_neo":
        session_mgr.show_status_text("Goodbye.")

    try:
        from core.error_recovery import is_circuit_open
        if is_circuit_open(name):
            from core.error_recovery import _registry
            msg = _registry.get_open_message(name)
            return types.FunctionResponse(
                id=fc.id, name=name, response={"result": msg},
            )
    except Exception:
        pass

    try:
        orch = _get_orchestrator()
        result = await orch.execute_tool_for_live(
            fc, session_mgr,
            on_finish=lambda n, res: _after_tool_result(
                n, res, show_progress=show_progress, session_mgr=session_mgr, audio=audio,
            ),
        )
        try:
            from core.error_recovery import _dashboard
            _dashboard.record_success(name)
        except Exception:
            pass
        return result
    except Exception as e:
        traceback.print_exc()
        session_mgr.show_status_text(f"Tool '{name}' failed: {e}")
        try:
            from core.error_recovery import record_error
            guidance = record_error(name, e)
            if guidance.get("circuit_open"):
                session_mgr.show_status_text(f"Tool '{name}' blocked.")
            elif guidance.get("fallback"):
                logger.warning("%s failed, fallback: %s", name, guidance["fallback"])
        except Exception:
            pass

    return types.FunctionResponse(
        id=fc.id, name=name,
        response={"result": f"Tool '{name}' returned no result."},
    )


def _after_tool_result(name: str, result, *, show_progress: bool, session_mgr, audio) -> None:
    """Callback after tool execution — handle UI state transitions."""
    if name in slow_tools() and show_progress:
        session_mgr.finish_processing()
    text = getattr(result, "text", "") or ""
    marker = "INSTALL_CONFIRMATION_JSON:"
    if marker in text:
        import json

        try:
            card = json.loads(text.split(marker, 1)[1].splitlines()[0])
            ui = getattr(session_mgr, "ui", None)
            if ui:
                ui.show_install_confirmation(
                    str(card["title"]), str(card["source"]), str(card["command"])
                )
            result.text = (
                f"A verified {card['source']} install is ready and the confirmation "
                f"card is visible. Ask exactly: Install {card['title']}?"
            )
            text = result.text
        except (KeyError, TypeError, ValueError):
            pass
    if text.startswith("CANCELLED_COMMAND:"):
        import re

        match = re.search(r"```(?:shell|bash|powershell)?\s*\n(.*?)\n```", text, re.S)
        ui = getattr(session_mgr, "ui", None)
        if match and ui:
            ui.show_command_response(f"```shell\n{match.group(1).strip()}\n```")
        result.text = (
            "The terminal command was not run and is already visible on screen. "
            "Reply exactly: I've put the command on screen."
        )
    if hasattr(result, "ok") and result.ok:
        if hasattr(result, "data") and result.data.get("silent"):
            return
    # Let the live session decide whether to speak based on the tool result
