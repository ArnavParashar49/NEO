"""GoalDispatcher — splits multi-task user input into independent goals
and dispatches each to a parallel agent via ThreadPoolExecutor.

Problem: "Check email AND tell me the weather AND find flights to Dubai"
  → The old swarm tried to decompose this as ONE goal into subtasks.
  → It ran them sequentially.
  → Each agent is a full ReAct loop — running them sequentially = 3× the wait.

Solution: Split into independent goals, dispatch each to a parallel agent.
  → All agents run SIMULTANEOUSLY in their own threads.
  → Total time = max(goal times), not sum.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from core.agent_loop import AgentResult, run_agent
from core.models import FAST_UTILITY
from core.sub_agents import (
    BUILT_IN_AGENTS,
    SubAgentSpec,
    _build_scoped_registry,
    MultiAgentResult,
    SubAgentTask,
    run_sub_agent,
)
from hybrid.registry import ToolRegistry
from hybrid.types import ExecutionContext


# --------------------------------------------------------------------------- #
# Goal splitting                                                              #
# --------------------------------------------------------------------------- #

_SPLIT_PROMPT = """Split the user's request into independent tasks that can be done in parallel.
"AND" and "also" usually separate independent tasks. Numbered lists ("1)... 2)...") are separate tasks.

Return ONLY valid JSON:
{
  "independent": true,
  "goals": ["task 1", "task 2", "task 3"]
}

If the request genuinely needs sequential steps (B depends on A's result), set independent: false.
Otherwise, split into the smallest number of independently-executable goals.
Keep each goal specific and self-contained — include all context needed to complete it alone."""


def split_goals(user_input: str) -> list[str]:
    """Split a multi-task user input into independent goal strings.

    Uses a fast model (Gemini Flash-Lite) for low-latency splitting.
    Returns the original input as a single-goal list if splitting fails.
    """
    text = (user_input or "").strip()
    if not text:
        return [text]

    # Quick pre-check: no conjunction markers → single goal
    markers = (" and ", " also ", " while you", " separately",
               "\n1.", "\n2.", "\n3.", "1) ", "2) ", "3) ",
               " AND ", " ALSO ")
    if not any(m in text.lower() if m.islower() else m in text for m in markers):
        return [text]

    try:
        from core.llm import ask_json

        result = ask_json(
            f"Split into independent tasks: {text}",
            model=FAST_UTILITY,
            system=_SPLIT_PROMPT,
            temperature=0.0,
        )
        if isinstance(result, dict) and result.get("independent", True):
            goals = result.get("goals", [text])
            if isinstance(goals, list) and len(goals) >= 2:
                return [g.strip() for g in goals if g.strip()]
        return [text]
    except Exception:
        return [text]


# --------------------------------------------------------------------------- #
# Agent assignment                                                            #
# --------------------------------------------------------------------------- #

# Keyword → agent_type mapping for quick assignment without an extra LLM call
_GOAL_AGENT_MAP: list[tuple[list[str], str]] = [
    (["email", "inbox", "gmail", "mail", "message", "whatsapp", "telegram",
      "contact", "calendar", "schedule", "remind", "event", "note",
      "send message", "send email"], "comms"),
    (["app", "open", "launch", "desktop", "file", "folder", "organize",
      "download", "system", "setting", "volume", "brightness", "mute",
      "browser", "install", "delete", "move", "copy", "rename"], "system_ops"),
    (["weather", "flight", "search", "research", "find", "compare",
      "news", "price", "look up", "what is", "who is", "define",
      "youtube", "video", "song", "play", "translate"], "researcher"),
    (["discuss", "analyze", "evaluate", "trade-off", "architecture",
      "best practice", "should i", "how to design", "review",
      "document", "docs", "api reference"], "analyst"),
]


def assign_agent(goal: str) -> str:
    """Pick the best agent type for a goal using keyword matching.

    Falls back to 'researcher' as the default (most general-purpose).
    """
    goal_lower = goal.lower()
    for keywords, agent_type in _GOAL_AGENT_MAP:
        if any(kw in goal_lower for kw in keywords):
            return agent_type
    return "researcher"


# --------------------------------------------------------------------------- #
# GoalDispatcher                                                              #
# --------------------------------------------------------------------------- #


@dataclass
class GoalResult:
    """Result for a single dispatched goal."""
    goal: str
    agent_type: str
    result: AgentResult | None = None
    error: str | None = None

    @property
    def answer(self) -> str:
        if self.error:
            return f"[{self.agent_type}] Error: {self.error}"
        if self.result:
            return self.result.answer
        return "No result."

    @property
    def ok(self) -> bool:
        return self.error is None and self.result is not None


@dataclass
class DispatchResult:
    """Aggregated results from a parallel dispatch."""
    results: list[GoalResult] = field(default_factory=list)
    summary: str = ""

    @property
    def all_ok(self) -> bool:
        return all(r.ok for r in self.results)


class GoalDispatcher:
    """Splits multi-goal input and dispatches each goal to a parallel agent.

    Each agent runs in its own thread with its own scoped tool registry
    and full ReAct loop. Independent goals complete simultaneously.
    """

    def __init__(self, max_workers: int = 5) -> None:
        self._max_workers = max_workers

    def dispatch(
        self,
        user_input: str,
        ctx: ExecutionContext | None = None,
        *,
        on_progress: callable | None = None,
    ) -> DispatchResult:
        """Split and execute all goals in parallel.

        Returns DispatchResult with per-goal results and a combined summary.
        """
        ctx = ctx or ExecutionContext()
        goals = split_goals(user_input)

        if len(goals) <= 1:
            return self._run_single(goals[0] if goals else user_input, ctx)

        return self._run_parallel(goals, ctx, on_progress=on_progress)

    def _run_single(self, goal: str, ctx: ExecutionContext) -> DispatchResult:
        agent_type = assign_agent(goal)
        spec = BUILT_IN_AGENTS.get(agent_type)
        if spec is None:
            spec = BUILT_IN_AGENTS.get("researcher") if BUILT_IN_AGENTS else None

        if spec is not None:
            result = run_sub_agent(spec, goal, ctx)
            gr = GoalResult(goal=goal, agent_type=agent_type, result=result)
        else:
            # No sub-agent — use the main agent loop directly
            result = run_agent(goal, ctx)
            gr = GoalResult(goal=goal, agent_type=agent_type, result=result)

        return DispatchResult(
            results=[gr],
            summary=gr.answer,
        )

    def _run_parallel(
        self,
        goals: list[str],
        ctx: ExecutionContext,
        *,
        on_progress: callable | None = None,
    ) -> DispatchResult:
        tasks: list[tuple[str, str, SubAgentSpec | None]] = []
        for goal in goals:
            agent_type = assign_agent(goal)
            spec = BUILT_IN_AGENTS.get(agent_type)
            tasks.append((goal, agent_type, spec))

        results: list[GoalResult] = [None] * len(tasks)

        def _execute(idx: int, goal: str, agent_type: str,
                     spec: SubAgentSpec | None) -> GoalResult:
            if on_progress:
                on_progress(agent_type, f"Starting: {goal[:80]}")
            try:
                if spec is not None:
                    result = run_sub_agent(spec, goal, ctx)
                else:
                    result = run_agent(goal, ctx)
                gr = GoalResult(goal=goal, agent_type=agent_type, result=result)
            except Exception as exc:
                gr = GoalResult(goal=goal, agent_type=agent_type, error=str(exc))
            if on_progress:
                status = "Done" if gr.ok else f"Failed: {gr.error}"
                on_progress(agent_type, status)
            return gr

        max_w = min(self._max_workers, len(tasks))
        with ThreadPoolExecutor(max_workers=max_w) as pool:
            futures = {
                pool.submit(_execute, i, goal, agent_type, spec): i
                for i, (goal, agent_type, spec) in enumerate(tasks)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:
                    goal, agent_type, _ = tasks[idx]
                    results[idx] = GoalResult(
                        goal=goal, agent_type=agent_type, error=str(exc),
                    )

        # Build a combined summary
        parts = []
        for gr in results:
            icon = "✅" if gr.ok else "⚠️"
            parts.append(f"{icon} **{gr.goal[:80]}** → {gr.answer[:200]}")
        summary = "\n".join(parts)

        return DispatchResult(results=results, summary=summary)


# Singleton
_dispatcher: GoalDispatcher | None = None


def get_dispatcher() -> GoalDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = GoalDispatcher()
    return _dispatcher
