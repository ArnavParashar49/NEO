"""Autonomous tool-use loop (ReAct-style) for ARIA.

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

DEFAULT_AGENT_MODEL = "gemini-2.5-flash"
DEFAULT_MAX_STEPS = 12
BUILD_MAX_STEPS = 40  # building a real multi-file project needs a bigger budget

# Tool results that must halt the loop and hand control back to the human.
_STOP_PREFIXES = ("NEEDS_CONFIRM", "NEEDS_USER")

AGENT_SYSTEM_PROMPT = """You are ARIA's autonomous task agent.

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


BUILD_SYSTEM_PROMPT = """You are ARIA's autonomous software builder.

You are given a goal (a project to build) and a project folder. Build a REAL, WORKING
project on your own — decide each step from real results.

How to work:
- Pick the simplest solid stack for the goal. Use web_search only if you genuinely
  need to check an API or approach.
- Write every source file with file_controller (action="create_file", an absolute
  path inside the given project folder, and the FULL real code in "content"). Write
  complete, runnable code — never placeholders or TODOs.
- After writing, INSTALL dependencies and RUN the project with dev_run (pass the
  project_dir). Read the real stdout/stderr it returns.
- If running fails, read the actual error, rewrite the offending file with
  file_controller, and run again. Repeat until it runs cleanly.
- For a static website, you don't need to run it — just create the files.
- Stay inside the given project folder. Do not touch unrelated files.

When the project runs cleanly (or is complete for a static site), STOP and reply with
a short summary of what you built and the exact command to run it. Do not keep going
once it works.
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
        session = GeminiToolSession(
            goal,
            system=AGENT_SYSTEM_PROMPT,
            tools=registry.to_gemini_declarations(),
        )

    steps: list[Step] = []
    call_counts: dict[tuple[str, str], int] = {}

    for _ in range(max_steps):
        turn = session.step()

        if not turn.calls:
            return AgentResult(answer=(turn.text or "").strip(), steps=steps, stopped_reason="done")

        for name, args in turn.calls:
            args = dict(args or {})

            # Loop guard: the same call 3x means the model is thrashing.
            sig = (name, repr(sorted(args.items())))
            call_counts[sig] = call_counts.get(sig, 0) + 1
            if call_counts[sig] > 3:
                return AgentResult(
                    answer=f"Stopping: '{name}' was called repeatedly without progress.",
                    steps=steps,
                    stopped_reason="loop_guard",
                )

            result = registry.invoke(name, args, ctx)
            step = Step(tool=name, args=args, result=result.text, ok=result.ok)
            steps.append(step)
            if on_step:
                on_step(step)

            # Human-in-the-loop: never auto-confirm destructive actions.
            if result.text.strip().startswith(_STOP_PREFIXES):
                return AgentResult(answer=result.text, steps=steps, stopped_reason="needs_user")

            session.add_tool_result(name, result.text)

    # Ran out of budget — ask the model to summarize where it landed.
    try:
        final = session.finalize()
    except Exception:
        final = "Reached the action limit before fully completing the goal."
    return AgentResult(answer=final, steps=steps, stopped_reason="max_steps")


# --------------------------------------------------------------------------- #
# Gemini glue — the only SDK-aware piece                                       #
# --------------------------------------------------------------------------- #

class GeminiToolSession:
    """A stateful function-calling conversation against google.genai."""

    def __init__(
        self,
        goal: str,
        *,
        system: str,
        tools: list[dict],
        model: str = DEFAULT_AGENT_MODEL,
        temperature: float | None = None,
    ) -> None:
        from google.genai import types

        from core.llm import _client

        self._types = types
        self._client = _client()
        self._model = model
        self._system = system
        self._temperature = temperature
        self._tools = [types.Tool(function_declarations=tools)] if tools else None
        self._contents: list[Any] = [
            types.Content(role="user", parts=[types.Part.from_text(text=goal)])
        ]

    def _config(self, *, with_tools: bool = True):
        return self._types.GenerateContentConfig(
            system_instruction=self._system,
            temperature=self._temperature,
            tools=self._tools if with_tools else None,
        )

    def step(self) -> Turn:
        resp = self._client.models.generate_content(
            model=self._model, contents=self._contents, config=self._config()
        )
        calls = list(getattr(resp, "function_calls", None) or [])
        if calls:
            # Record the model's turn so the following tool responses align.
            self._contents.append(resp.candidates[0].content)
            return Turn(calls=[(fc.name, dict(fc.args or {})) for fc in calls])
        return Turn(text=resp.text or "")

    def add_tool_result(self, name: str, result: str) -> None:
        part = self._types.Part.from_function_response(name=name, response={"result": result})
        self._contents.append(self._types.Content(role="user", parts=[part]))

    def finalize(self) -> str:
        self._contents.append(
            self._types.Content(
                role="user",
                parts=[self._types.Part.from_text(
                    text="You've hit the action limit. In one or two sentences, summarize "
                         "what you accomplished and what (if anything) remains."
                )],
            )
        )
        resp = self._client.models.generate_content(
            model=self._model, contents=self._contents, config=self._config(with_tools=False)
        )
        return (resp.text or "Reached the action limit.").strip()


# --------------------------------------------------------------------------- #
# Autonomous project builder — a scoped variant of the loop                    #
# --------------------------------------------------------------------------- #

_BUILD_TOOLS = ("file_controller", "web_search", "code_helper")


def build_registry() -> ToolRegistry:
    """A curated registry for the build loop: write files, research, run & test.

    Keeps the command runner (dev_run) OUT of the global, always-on toolset — it
    only exists inside an explicitly-confirmed build.
    """
    glob = ToolRegistry.instance()
    sub = ToolRegistry()
    for name in _BUILD_TOOLS:
        tool = glob.lookup(name)
        if tool is not None:
            sub._tools[name] = tool

    from actions.dev_run import dev_run as _dev_run

    sub.register(
        name="dev_run",
        description=(
            "Run a shell command INSIDE the project folder to install dependencies or "
            "run/test the app, e.g. 'pip install flask', 'python main.py', 'npm install'. "
            "Returns the real stdout/stderr + exit code so you can read errors and fix them."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "command": {"type": "STRING", "description": "Command, e.g. 'python main.py' or 'pip install flask'"},
                "project_dir": {"type": "STRING", "description": "Absolute path to the project folder"},
                "timeout": {"type": "INTEGER", "description": "Max seconds (default 60)"},
            },
            "required": ["command", "project_dir"],
        },
        handler=lambda args, ctx: _dev_run(parameters=args, player=getattr(ctx, "ui", None)),
        category="dev",
        agent="system",
    )
    return sub


def run_build(
    goal: str,
    ctx: ExecutionContext | None = None,
    *,
    on_step: Callable[[Step], None] | None = None,
    max_steps: int = BUILD_MAX_STEPS,
) -> AgentResult:
    """Run the autonomous build loop with the build prompt + curated tools."""
    registry = build_registry()
    session = GeminiToolSession(
        goal,
        system=BUILD_SYSTEM_PROMPT,
        tools=registry.to_gemini_declarations(),
    )
    return run_agent(
        goal, ctx,
        registry=registry,
        session=session,
        max_steps=max_steps,
        on_step=on_step,
    )
