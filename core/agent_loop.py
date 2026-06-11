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

# Gemini 2.5 "thinking" budgets (tokens). The free tier has no pro access, so the
# quality lever is letting flash reason before it acts. The plan is a one-shot,
# high-leverage call (bigger budget); each build step gets a modest budget.
BUILD_PLAN_THINKING = 8192
BUILD_STEP_THINKING = 2048

# Cap a single tool result fed back into context. Long dev_run logs would
# otherwise crowd out the plan and earlier files, making flash lose the thread.
_MAX_RESULT_CHARS = 4000

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


BUILD_SYSTEM_PROMPT = """You are ARIA's autonomous software builder — a senior engineer
who ships small, real, working projects end to end.

You are given a goal, a project folder, and a build plan you already wrote. Follow the
plan, but adapt it from real results. Build something a real person would actually use,
not a toy stub.

Quality bar (this is what "smart" means — do not skip it):
- Write COMPLETE, production-quality code for every file. Never leave placeholders,
  TODOs, `pass`, "implement later", or stubbed functions. If you name it, you build it.
- Deliver the real features the goal implies, not a hello-world. Handle the obvious
  edge cases and errors. Make the UI/output look intentional, not default.
- Keep the project coherent: file names, imports, routes, and config must line up
  across files. Re-read a file with file_controller (action="read_file") if unsure
  what you wrote.
- Always include a short README.md with what it is and the exact run command, and a
  dependency manifest (requirements.txt / package.json) when there are dependencies.

How to work — decide ONE action at a time and read the real result:
- Pick the simplest stack that fully satisfies the goal. Use web_search only to check
  a specific unfamiliar API or version — don't research what you already know.
- Write each file with file_controller (action="create_file", an absolute path inside
  the given project folder, the FULL real code in "content").
- After the files exist, INSTALL dependencies and RUN the project with dev_run (pass
  project_dir). Read the real stdout/stderr/exit code.
- If it fails, read the ACTUAL error, fix the specific file(s) with file_controller,
  and run again. Repeat until it runs cleanly with no traceback.
- For a static website you don't need to run a server — just create the files and open
  the entry point once with dev_run if useful.
- Stay strictly inside the given project folder. Never touch unrelated files.

When the project runs cleanly (or is complete for a static site), STOP and reply with a
short summary of what you built, the file layout, and the exact command to run it. Do
not keep going once it genuinely works — but do not stop early on a half-built project.
"""


BUILD_PLAN_PROMPT = """You are a senior software architect. Given a build request and a
target folder, produce a SHORT, concrete build plan for a small but real, working project.

Return PLAIN TEXT (no markdown headers, no code) in exactly this shape:

Stack: <language + key libraries/framework, and why in <=10 words>
Files:
- <relative/path> — <one line: what it contains>
- <relative/path> — <one line>
(list every file you will create, including README.md and any requirements.txt/package.json)
Features: <the 2-5 real capabilities the project must actually have>
Run: <the exact command(s) to install deps and run it>

Keep it tight and buildable in a handful of files. Choose the simplest stack that
delivers the real features. Do not write code here — just the plan."""


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
        thinking_budget: int | None = None,
    ) -> None:
        from google.genai import types

        from core.llm import _client

        self._types = types
        self._client = _client()
        self._model = model
        self._system = system
        self._temperature = temperature
        self._thinking_budget = thinking_budget
        self._tools = [types.Tool(function_declarations=tools)] if tools else None
        self._contents: list[Any] = [
            types.Content(role="user", parts=[types.Part.from_text(text=goal)])
        ]

    def _config(self, *, with_tools: bool = True):
        thinking = (
            self._types.ThinkingConfig(thinking_budget=self._thinking_budget)
            if self._thinking_budget is not None
            else None
        )
        return self._types.GenerateContentConfig(
            system_instruction=self._system,
            temperature=self._temperature,
            tools=self._tools if with_tools else None,
            thinking_config=thinking,
        )

    def _generate(self, *, with_tools: bool = True):
        """generate_content with light backoff on transient free-tier limits.

        Free Gemini keys hit per-minute 429s and 503 "high demand" spikes often;
        without this a single blip aborts the whole build. Daily-quota 429s won't
        recover in three tries and still propagate (so the caller can fall back).
        """
        import time

        last: Exception | None = None
        for attempt in range(3):
            try:
                return self._client.models.generate_content(
                    model=self._model,
                    contents=self._contents,
                    config=self._config(with_tools=with_tools),
                )
            except Exception as e:  # noqa: BLE001 — narrow by message below
                last = e
                msg = str(e)
                if not any(code in msg for code in ("429", "503", "500", "UNAVAILABLE")):
                    raise
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
        raise last  # type: ignore[misc]

    def step(self) -> Turn:
        resp = self._generate()
        calls = list(getattr(resp, "function_calls", None) or [])
        if calls:
            # Record the model's turn so the following tool responses align.
            self._contents.append(resp.candidates[0].content)
            return Turn(calls=[(fc.name, dict(fc.args or {})) for fc in calls])
        return Turn(text=resp.text or "")

    def add_tool_result(self, name: str, result: str) -> None:
        text = result or ""
        if len(text) > _MAX_RESULT_CHARS:
            head = text[: _MAX_RESULT_CHARS // 2]
            tail = text[-_MAX_RESULT_CHARS // 2 :]
            text = f"{head}\n…[{len(result) - _MAX_RESULT_CHARS} chars trimmed]…\n{tail}"
        part = self._types.Part.from_function_response(name=name, response={"result": text})
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
        resp = self._generate(with_tools=False)
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


def plan_build(goal: str) -> str:
    """Architect a concrete build plan before any code is written.

    A one-shot, thinking-on call: deciding the stack and file layout up front keeps
    a multi-file project coherent instead of flash improvising file-by-file.
    Returns plain-text plan, or "" if planning fails (the loop still runs).
    """
    import time

    from core.llm import ask

    for attempt in range(3):
        try:
            return ask(
                goal,
                model=DEFAULT_AGENT_MODEL,
                system=BUILD_PLAN_PROMPT,
                temperature=0.2,
                thinking_budget=BUILD_PLAN_THINKING,
            ).strip()
        except Exception as e:  # noqa: BLE001
            if attempt < 2 and any(c in str(e) for c in ("429", "503", "500", "UNAVAILABLE")):
                time.sleep(2 * (attempt + 1))
                continue
            return ""
    return ""


def run_build(
    goal: str,
    ctx: ExecutionContext | None = None,
    *,
    on_step: Callable[[Step], None] | None = None,
    on_plan: Callable[[str], None] | None = None,
    max_steps: int = BUILD_MAX_STEPS,
) -> AgentResult:
    """Run the autonomous build loop: architect a plan, then build against it."""
    registry = build_registry()

    plan = plan_build(goal)
    if plan and on_plan:
        try:
            on_plan(plan)
        except Exception:
            pass

    build_goal = goal if not plan else f"{goal}\n\n--- Your build plan ---\n{plan}"
    session = GeminiToolSession(
        build_goal,
        system=BUILD_SYSTEM_PROMPT,
        tools=registry.to_gemini_declarations(),
        thinking_budget=BUILD_STEP_THINKING,
    )
    return run_agent(
        build_goal, ctx,
        registry=registry,
        session=session,
        max_steps=max_steps,
        on_step=on_step,
    )
