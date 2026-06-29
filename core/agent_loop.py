"""Autonomous tool-use loop (ReAct-style) for NEO.

Instead of routing by regex or pre-planning a fixed list of independent steps,
this gives the model a goal + the live tool schemas and lets it decide the next
action, observe the *real* result, and decide again — until the goal is met.

Design:
- The loop logic (`run_agent`) is SDK-free and depends on a small `ToolSession`
  interface, so it can be unit-tested with a fake session (no network).
- `GeminiToolSession` is the only piece that touches the google.genai SDK.
- Safety is structural, not prompt-dependent:
    * a hard step budget,
    * a repeated-identical-call guard (kills thrash loops),
    * a deterministic human-in-the-loop STOP whenever a tool returns
      NEEDS_CONFIRM / NEEDS_USER — the loop never auto-confirms destructive ops.

Enable it by setting ``"autonomous_mode": true`` in config/api_keys.json; until
then the existing planner path is used and this module is dormant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from hybrid.registry import ToolRegistry
from hybrid.types import ExecutionContext

from core.models import PRIMARY as DEFAULT_AGENT_MODEL
DEFAULT_MAX_STEPS = 12

# Cap a single tool result fed back into context. Long dev_run logs would
# otherwise crowd out the plan and earlier files, making flash lose the thread.
_MAX_RESULT_CHARS = 4000

# Tool results that must halt the loop and hand control back to the human.
_STOP_PREFIXES = ("NEEDS_CONFIRM", "NEEDS_USER")

AGENT_SYSTEM_PROMPT = """You are NEO's autonomous task agent.

You are given a goal and a set of tools. Work toward the goal by deciding ONE next
action at a time: call the most appropriate tool, observe the real result, then
decide the next action based on what actually happened.

Principles:
- Use real tool results — never assume a tool succeeded; read its output.
- Prefer the fewest actions that fully achieve the goal.
- After each result, check whether the goal is met. If it is, STOP and reply with a
  short, natural summary (no tool call).
- If a tool fails, adapt — try a different tool or approach. Do not repeat the same
  failing call.
- If a tool result says NEEDS_CONFIRM or NEEDS_USER, do not try to work around it —
  stop and let the user decide.
- If you are missing information only the user can provide, ask one concise question
  instead of guessing.
"""


# --------------------------------------------------------------------------- #
# Results & the session interface the loop depends on                          #
# --------------------------------------------------------------------------- #

@dataclass
class Step:
    tool: str
    args: dict[str, Any]
    result: str
    ok: bool


@dataclass
class AgentResult:
    answer: str
    steps: list[Step] = field(default_factory=list)
    stopped_reason: str = "done"  # done | max_steps | needs_user | loop_guard


@dataclass
class Turn:
    """One decision from the model: either tool calls, or a final text answer."""
    calls: list[tuple[str, dict]] = field(default_factory=list)
    text: str | None = None


class ToolSession(Protocol):
    def step(self) -> Turn: ...
    def add_tool_result(self, name: str, result: str) -> None: ...
    def finalize(self) -> str: ...


# --------------------------------------------------------------------------- #
# The loop — no SDK, fully testable                                            #
# --------------------------------------------------------------------------- #

def run_agent(
    goal: str,
    ctx: ExecutionContext | None = None,
    *,
    registry: ToolRegistry | None = None,
    session: ToolSession | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    on_step: Callable[[Step], None] | None = None,
) -> AgentResult:
    registry = registry or ToolRegistry.instance()
    ctx = ctx or ExecutionContext()
    if session is None:
        try:
            from core.memory_rag import format_memory_for_prompt
            memory_context = format_memory_for_prompt(goal)
        except ImportError:
            memory_context = ""
            
        import platform, getpass, datetime
        sys_ctx = f"\n\n[SYSTEM CONTEXT]\nOperating System: {platform.system()} {platform.release()}\nOS Account Name: {getpass.getuser()}\nCurrent Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
        # Inject memory context if available
        try:
            from core.memory_graph import format_graph_for_prompt
            sys_ctx += format_graph_for_prompt()
        except Exception:
            pass
        
        full_system = AGENT_SYSTEM_PROMPT + sys_ctx
        if memory_context:
            full_system += f"\n\n{memory_context}"

        session = GeminiToolSession(
            goal,
            system=full_system,
            tools=registry.to_gemini_declarations(),
        )

    steps: list[Step] = []
    call_counts: dict[tuple[str, str], int] = {}

    for _ in range(max_steps):
        turn = session.step()

        if not turn.calls:
            res = AgentResult(answer=(turn.text or "").strip(), steps=steps, stopped_reason="done")
            try:
                from hybrid.task_bus import get_task_bus
                get_task_bus().emit("task.completed", {"goal": goal, "steps": steps, "result": res})
            except Exception: pass
            return res

        for name, args in turn.calls:
            args = dict(args or {})

            # Loop guard: the same call 3x means the model is thrashing.
            sig = (name, repr(sorted(args.items())))
            call_counts[sig] = call_counts.get(sig, 0) + 1
            if call_counts[sig] > 3:
                res = AgentResult(
                    answer=f"Stopping: '{name}' was called repeatedly without progress.",
                    steps=steps,
                    stopped_reason="loop_guard",
                )
                try:
                    from hybrid.task_bus import get_task_bus
                    get_task_bus().emit("task.completed", {"goal": goal, "steps": steps, "result": res})
                except Exception: pass
                return res

            result = registry.invoke(name, args, ctx)
            step = Step(tool=name, args=args, result=result.text, ok=result.ok)
            steps.append(step)
            if on_step:
                on_step(step)

            # Human-in-the-loop: never auto-confirm destructive actions.
            if result.text.strip().startswith(_STOP_PREFIXES):
                res = AgentResult(answer=result.text, steps=steps, stopped_reason="needs_user")
                try:
                    from hybrid.task_bus import get_task_bus
                    get_task_bus().emit("task.completed", {"goal": goal, "steps": steps, "result": res})
                except Exception: pass
                return res

            session.add_tool_result(name, result.text)

    # Ran out of budget — ask the model to summarize where it landed.
    try:
        final = session.finalize()
    except Exception:
        final = "Reached the action limit before fully completing the goal."
    res = AgentResult(answer=final, steps=steps, stopped_reason="max_steps")
    
    try:
        from hybrid.task_bus import get_task_bus
        get_task_bus().emit("task.completed", {"goal": goal, "steps": steps, "result": res})
    except Exception:
        pass
        
    return res


# --------------------------------------------------------------------------- #
# Gemini glue — the only SDK-aware piece                                       #
# --------------------------------------------------------------------------- #

class GeminiToolSession:
    """A stateful function-calling conversation against litellm."""

    def __init__(
        self,
        goal: str,
        *,
        system: str,
        tools: list[dict],
        model: str = DEFAULT_AGENT_MODEL,
        temperature: float | None = None,
        thinking_budget: int | None = None,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._thinking_budget = thinking_budget
        
        # Tools in litellm format (OpenAI format)
        self._tools = [{"type": "function", "function": t} for t in tools] if tools else None
        
        self._messages: list[dict] = []
        if system:
            self._messages.append({"role": "system", "content": system})
        self._messages.append({"role": "user", "content": goal})

    def _generate(self, *, with_tools: bool = True):
        import time
        import litellm
        from core.llm import get_provider_router, _get_api_key_for_model, _route_provider

        # Normalize the primary model name (litellm needs gemini/ prefix)
        primary = self._model
        if primary.startswith("gemini-"):
            primary = f"gemini/{primary}"

        chain = get_provider_router().get_fallback_chain(primary)

        base_kwargs: dict = {"messages": self._messages}
        if with_tools and self._tools:
            base_kwargs["tools"] = self._tools
        if self._temperature is not None:
            base_kwargs["temperature"] = self._temperature

        last: Exception | None = None
        for model in chain:
            kwargs = {**base_kwargs, "model": model}
            # Use _route_provider to correctly resolve cometapi/kimi/groq/openrouter keys
            try:
                routed_model, api_key, api_base = _route_provider(model)
                kwargs["model"] = routed_model
                if api_key:
                    kwargs["api_key"] = api_key
                if api_base:
                    kwargs["api_base"] = api_base
            except Exception:
                # If routing fails, fall back to simple key lookup
                api_key = _get_api_key_for_model(model)
                if api_key:
                    kwargs["api_key"] = api_key
            try:
                return litellm.completion(**kwargs)
            except Exception as e:
                last = e
                time.sleep(1)  # Brief pause before trying next model
        raise last
    def step(self) -> Turn:
        resp = self._generate()
        msg = resp.choices[0].message
        
        # litellm returns message dict-like object
        calls = []
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            import json
            for tc in msg.tool_calls:
                args = {}
                if hasattr(tc.function, "arguments"):
                    try:
                        args = json.loads(tc.function.arguments)
                    except Exception:
                        if isinstance(tc.function.arguments, dict):
                            args = tc.function.arguments
                calls.append((tc.function.name, args))
                
            self._messages.append(msg.model_dump())
            return Turn(calls=calls)
            
        self._messages.append(msg.model_dump())
        return Turn(text=msg.content or "")

    def add_tool_result(self, name: str, result: str) -> None:
        text = result or ""
        if len(text) > _MAX_RESULT_CHARS:
            head = text[: _MAX_RESULT_CHARS // 2]
            tail = text[-_MAX_RESULT_CHARS // 2 :]
            text = f"{head}\n…[{len(result) - _MAX_RESULT_CHARS} chars trimmed]…\n{tail}"
            
        # Find the tool_call_id for this name from the last assistant message
        tool_call_id = "unknown"
        for m in reversed(self._messages):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m.get("tool_calls"):
                    func = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
                    func_name = func.get("name") if isinstance(func, dict) else getattr(func, "name", None)
                    if func_name == name:
                        tool_call_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", "unknown")
                        break
                if tool_call_id != "unknown":
                    break
                    
        import json
        
        # Inject reflection hint if result indicates failure or empty findings
        lower_text = text.lower()
        if "not found" in lower_text or ("no " in lower_text and " found" in lower_text) or "error" in lower_text or "failed" in lower_text or "access denied" in lower_text:
            text += "\n[SYSTEM HINT: The previous action failed or yielded no results. Do NOT repeat the exact same action. Think step-by-step about why it failed. Try a broader search, a different path, or an alternative approach.]"

        self._messages.append({
            "role": "tool",
            "tool_call_id": str(tool_call_id),
            "name": name,
            "content": json.dumps({"result": text})
        })

    def finalize(self) -> str:
        self._messages.append({
            "role": "user",
            "content": "You've hit the action limit. In one or two sentences, summarize what you accomplished and what (if anything) remains."
        })
        resp = self._generate(with_tools=False)
        return (resp.choices[0].message.content or "Reached the action limit.").strip()



