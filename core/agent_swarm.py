"""Unified Agent Swarm — agent-to-agent conversation, dynamic team formation,
and orchestrated multi-agent workflows for NEO.

Replaces the fragmented approach of:
  - core/sub_agents.py (isolated ReAct loops, no inter-agent talk)
  - hybrid/agents/ (TaskBus stubs, no real handoff)

with a single, cohesive swarm protocol.
"""

from __future__ import annotations

import re as _re
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from core.agent_loop import (
    DEFAULT_MAX_STEPS,
    AgentResult,
    GeminiToolSession,
    Step,
    run_agent,
)
from hybrid.registry import ToolRegistry
from hybrid.types import ExecutionContext


# ========================================================================= #
# Conversation Protocol                                                      #
# ========================================================================= #


class Intent(str, Enum):
    """Well-known intents for agent-to-agent communication."""

    ASK = "ask"
    ANSWER = "answer"
    DELEGATE = "delegate"
    TASK_DONE = "task_done"
    PROPOSE = "propose"
    AGREE = "agree"
    COUNTER = "counter"
    ERROR = "error"
    INFO = "info"
    HANDOFF = "handoff"


@dataclass
class SwarmMessage:
    """A message in the agent conversation protocol."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    sender: str = ""
    recipient: str | None = None
    intent: Intent = Intent.INFO
    content: str = ""
    confidence: float = 1.0
    thread_id: str = ""
    timestamp: float = field(default_factory=time.time)

    def __repr__(self) -> str:
        return (
            f"[{self.sender}→{self.recipient or 'ALL'} "
            f"({self.intent.value})] {self.content[:80]}"
        )


# ========================================================================= #
# Conversation Channel                                                       #
# ========================================================================= #


class ConversationChannel:
    """Thread-safe message channel agents use to talk to each other."""

    def __init__(self, max_history: int = 500) -> None:
        self._lock = threading.Lock()
        self._messages: list[SwarmMessage] = []
        self._max_history = max_history
        self._subscribers: dict[str, list[Callable[[SwarmMessage], None]]] = (
            defaultdict(list)
        )

    def send(self, msg: SwarmMessage) -> None:
        """Post a message to the channel."""
        with self._lock:
            self._messages.append(msg)
            if len(self._messages) > self._max_history:
                self._messages = self._messages[-self._max_history :]

        handlers: list[Callable] = []
        with self._lock:
            if msg.recipient:
                handlers.extend(self._subscribers.get(msg.recipient, []))
            handlers.extend(self._subscribers.get("*", []))
            if msg.thread_id:
                handlers.extend(
                    self._subscribers.get(f"thread:{msg.thread_id}", [])
                )

        for handler in handlers:
            try:
                handler(msg)
            except Exception:
                pass

    def subscribe(
        self,
        agent_name: str,
        handler: Callable[[SwarmMessage], None],
    ) -> None:
        """Subscribe an agent to receive messages addressed to it."""
        with self._lock:
            self._subscribers[agent_name].append(handler)

    def get_thread(self, thread_id: str) -> list[SwarmMessage]:
        """Return all messages in a conversation thread."""
        return [m for m in self._messages if m.thread_id == thread_id]


# ========================================================================= #
# Swarm Agent Specs                                                          #
# ========================================================================= #


@dataclass
class SwarmAgentSpec:
    """Blueprint for a swarm agent with conversation capabilities."""

    name: str
    system_prompt: str
    allowed_tools: list[str]
    max_steps: int = DEFAULT_MAX_STEPS
    can_converse: bool = True
    delegation_targets: list[str] = field(default_factory=list)


SWARM_AGENTS: dict[str, SwarmAgentSpec] = {}

# Populate built-in swarm agents
SWARM_AGENTS.update({
    "researcher": SwarmAgentSpec(
        name="researcher",
        system_prompt=(
            "You are NEO's Research Agent. Gather, compare, and summarise "
            "information from the web.\n\n"
            "Principles:\n"
            "- Search broadly first, then drill into specifics.\n"
            "- Always cite sources (URLs) in your summary.\n"
            "- Return concise answers — not raw search dumps.\n"
            "- If you can't find reliable information, say so honestly.\n\n"
            "Conversation: If you need system ops, DELEGATE to 'system_ops'. "
            "If communication is needed, DELEGATE to 'comms'. "
            "If stuck, ASK your teammates for help."
        ),
        allowed_tools=[
            "web_search", "browser_control", "youtube_video", "flight_finder",
        ],
        max_steps=15,
        delegation_targets=["system_ops", "comms", "analyst"],
    ),
    "system_ops": SwarmAgentSpec(
        name="system_ops",
        system_prompt=(
            "You are NEO's System Operations Agent. Handle OS-level tasks:\n"
            "opening apps, browsing to URLs, managing files, adjusting settings.\n\n"
            "CRITICAL PRINCIPLES:\n"
            "- For 'open <website>' or 'go to <site>' requests: call browser_control "
            "with action='go_to' and the EXACT URL directly. Do NOT web_search first.\n"
            "- Only call web_search if the user asks for information (news, prices, etc.).\n"
            "- For known brands/sites, use canonical URLs (rockstargames.com, youtube.com, "
            "gmail.com, notion.so). Don't research what you already know.\n"
            "- To write something in Notepad: Use file_controller to create a text file on the Desktop, then use computer_control or open_app to open that file in Notepad.\n"
            "- Confirm destructive actions (deletes, moves).\n"
            "- Return clear success/failure status.\n\n"
            "Conversation: If you need information, ASK 'researcher'."
        ),
        allowed_tools=[
            "open_app", "system_control", "computer_settings", "window_control",
            "project_starter",
            "desktop_control", "computer_control", "file_controller",
            "organizer_control", "document_tools", "list_manager",
            "notes_control", "calendar_control", "reminder",
            "browser_control", "download_control",
        ],
        max_steps=12,
        delegation_targets=["researcher", "analyst"],
    ),
    "comms": SwarmAgentSpec(
        name="comms",
        system_prompt=(
            "You are NEO's Communications Agent. Handle emails, messages, "
            "calendar, reminders, and contacts.\n\n"
            "Principles:\n"
            "- Look up contacts before sending.\n"
            "- Draft clearly, confirm before sending.\n"
            "- Include all relevant details in calendar events.\n\n"
            "Conversation: If you need research, ASK 'researcher'. "
            "If you need analysis of content, ASK 'analyst'."
        ),
        allowed_tools=[
            "send_email", "send_message", "contact_manager",
            "calendar_control", "reminder", "notes_control",
        ],
        max_steps=10,
        delegation_targets=["researcher", "analyst"],
    ),
    "analyst": SwarmAgentSpec(
        name="analyst",
        system_prompt=(
            "You are NEO's Analyst. Discuss projects, evaluate trade-offs, "
            "search documentation, help the user think through ideas.\n\n"
            "Principles:\n"
            "- Recommend specific approaches with reasons — be opinionated.\n"
            "- Present trade-offs for multiple valid options.\n"
            "- Search authoritative docs and cite sources.\n"
            "- Discuss architecture, not implementation — never write code.\n\n"
            "Conversation: If you need research, ASK 'researcher'. "
            "If system ops are needed, DELEGATE to 'system_ops'."
        ),
        allowed_tools=[
            "discuss_project", "search_docs", "web_search",
            "file_controller", "screen_analyze",
        ],
        max_steps=12,
        delegation_targets=["researcher", "system_ops"],
    ),
    "reviewer": SwarmAgentSpec(
        name="reviewer",
        system_prompt=(
            "You are NEO's Reviewer. After other agents produce output, "
            "you check it for correctness, edge cases, and quality.\n\n"
            "Principles:\n"
            "- Verify output meets the original goal.\n"
            "- Flag errors, incomplete work, or risky patterns.\n"
            "- Be specific about what's wrong and how to fix it.\n"
            "- If excellent, say so concisely."
        ),
        allowed_tools=["file_controller", "web_search"],
        max_steps=5,
        delegation_targets=[],
    ),
    "gui_worker": SwarmAgentSpec(
        name="gui_worker",
        system_prompt=(
            "You are NEO's GUI Automation Agent (Computer User). You physically interact with the screen.\n\n"
            "FAST WORKFLOW (Windows, macOS, Linux):\n"
            "1. Run open_app to launch the application.\n"
            "2. Run gui_control(action='inspect_window', title='App Name') to get a map of the UI elements.\n"
            "3. Run gui_control(action='click_element', element_id='1-2') or type_text to interact.\n\n"
            "SLOW FALLBACK (Only if inspect_window fails/is blocked):\n"
            "1. Run screen_analyze(action='find_element', query='target element name'). This returns normalized [y, x] coordinates.\n"
            "2. Run gui_control(action='click_at', x=..., y=...) to click.\n"
            "3. Run gui_control(action='type_text', text=...) to type globally.\n\n"
            "CRITICAL PRINCIPLES FOR BEING SMART:\n"
            "- You are a strict GUI operator. You DO NOT have background terminal access. If you need to run a command, you MUST physically open a terminal inside the app (e.g. Ctrl+`) and type it out using gui_control.\n"
            "- Apps have states! If you open an app and don't see the element you want, READ the UI tree to figure out where you are (e.g. Welcome Screen, Login Screen).\n"
            "- UNIVERSAL REASONING: You are a smart AI. Do NOT just give up if a button is missing. Reason about the app's current state and navigate to the right state.\n"
            "- INFER SHORTCUTS: Use your vast knowledge of software to deduce standard keyboard shortcuts for ANY app (e.g., Ctrl+T for tabs, Ctrl+F for search, Ctrl+N for new file, Ctrl+L for AI chat, Ctrl+` for terminal). Use gui_control(action='press_key') to bypass tedious clicking.\n"
            "- VERIFY: After clicking something or pressing a key, run inspect_window AGAIN to see if the UI changed.\n"
            "- ALWAYS try inspect_window first. NEVER use screen_analyze unless inspect_window explicitly fails (vision is 10x slower).\n"
            "- Always wait 1-2 seconds between clicks if navigating menus.\n"
        ),
        allowed_tools=["open_app", "gui_control", "screen_analyze"],
        max_steps=20,
        delegation_targets=["researcher", "system_ops"],
    ),
})


# ========================================================================= #
# Swarm Runner                                                               #
# ========================================================================= #


def _build_agent_registry(spec: SwarmAgentSpec) -> ToolRegistry:
    """Build a scoped tool registry for a swarm agent."""
    global_registry = ToolRegistry.instance()
    agent_registry = ToolRegistry()
    for tool_name in spec.allowed_tools:
        tool = global_registry.lookup(tool_name)
        if tool is not None:
            agent_registry._tools[tool_name] = tool
    return agent_registry


_CONV_PATTERNS = [
    (r"SWARM_ASK:(\w+):(.+)", Intent.ASK),
    (r"SWARM_DELEGATE:(\w+):(.+)", Intent.DELEGATE),
    (r"SWARM_ANSWER:(\w+):(.+)", Intent.ANSWER),
    (r"SWARM_PROPOSE:(\w+):(.+)", Intent.PROPOSE),
]


def run_swarm_agent(
    spec: SwarmAgentSpec,
    goal: str,
    ctx: ExecutionContext | None = None,
    *,
    channel: ConversationChannel | None = None,
    thread_id: str = "",
    on_step: Callable[[Step], None] | None = None,
) -> AgentResult:
    """Run a single swarm agent with conversation capabilities."""
    ctx = ctx or ExecutionContext()
    registry = _build_agent_registry(spec)

    conversation_block = ""
    if spec.can_converse and channel is not None:
        available = (
            ", ".join(spec.delegation_targets)
            if spec.delegation_targets
            else "none"
        )
        conversation_block = (
            f"\n\nCONVERSATION PROTOCOL:\n"
            f"Your teammates: {available}.\n"
            f"When you need help, include a line: "
            f"SWARM_ASK:<agent_name>:<your question>\n"
            f"To delegate work: "
            f"SWARM_DELEGATE:<agent_name>:<task description>\n"
            f"To answer a question: "
            f"SWARM_ANSWER:<agent_name>:<your answer>\n"
            f"These are routed automatically to the right agent.\n"
        )

    enhanced_prompt = spec.system_prompt + conversation_block
    session = GeminiToolSession(
        goal,
        system=enhanced_prompt,
        tools=registry.to_gemini_declarations(),
    )

    def _on_step_with_conv(step: Step) -> None:
        result_text = step.result or ""
        for pattern, intent in _CONV_PATTERNS:
            match = _re.search(pattern, result_text)
            if match and channel is not None:
                target = match.group(1).strip()
                content = match.group(2).strip()
                channel.send(SwarmMessage(
                    sender=spec.name,
                    recipient=target,
                    intent=intent,
                    content=content,
                    thread_id=thread_id,
                ))
        if on_step:
            on_step(step)

    print(
        f"[Swarm:{spec.name}] 🚀 Starting | Tools: {len(registry._tools)} "
        f"| Goal: {goal[:80]}"
    )
    result = run_agent(
        goal, ctx,
        registry=registry,
        session=session,
        max_steps=spec.max_steps,
        on_step=_on_step_with_conv if channel else on_step,
    )
    print(
        f"[Swarm:{spec.name}] ✅ Done ({len(result.steps)} steps, "
        f"reason: {result.stopped_reason})"
    )

    if channel is not None and thread_id:
        channel.send(SwarmMessage(
            sender=spec.name,
            recipient="orchestrator",
            intent=Intent.TASK_DONE,
            content=result.answer[:500],
            thread_id=thread_id,
        ))

    return result


# ========================================================================= #
# Swarm Orchestrator                                                         #
# ========================================================================= #


@dataclass
class SwarmTask:
    """A unit of work for the swarm."""
    agent_type: str
    goal: str
    thread_id: str = ""
    depends_on: list[str] = field(default_factory=list)


@dataclass
class SwarmResult:
    """Aggregated results from a swarm run."""
    results: dict[str, AgentResult] = field(default_factory=dict)
    conversation: list[SwarmMessage] = field(default_factory=list)
    summary: str = ""
    success: bool = False


@dataclass
class SwarmPlan:
    """A plan produced by the swarm orchestrator."""
    tasks: list[SwarmTask]
    review_needed: bool = True


class SwarmOrchestrator:
    """Plans and executes multi-agent workflows."""

    def __init__(self) -> None:
        self.channel = ConversationChannel()

    def decompose(self, goal: str) -> SwarmPlan:
        """Keyword-based decomposition of a goal into swarm tasks."""
        goal_lower = goal.lower()
        thread_id = str(uuid.uuid4())[:8]
        tasks: list[SwarmTask] = []

        if any(w in goal_lower for w in (
            "research", "search", "find", "compare", "weather",
            "flight", "news", "price", "look up", "what is",
        )):
            tasks.append(SwarmTask(
                agent_type="researcher",
                goal=f"Research and report: {goal}",
                thread_id=thread_id,
            ))

        if any(w in goal_lower for w in (
            "email", "message", "contact", "calendar",
            "remind", "note", "send",
        )):
            tasks.append(SwarmTask(
                agent_type="comms",
                goal=f"Handle communication: {goal}",
                thread_id=thread_id,
            ))

        if any(w in goal_lower for w in (
            "discuss", "plan", "architecture", "trade-off",
            "evaluate", "analyze", "best practice", "should i",
        )):
            tasks.append(SwarmTask(
                agent_type="analyst",
                goal=f"Analyze and discuss: {goal}",
                thread_id=thread_id,
            ))

        if any(w in goal_lower for w in (
            "file", "folder", "organize", "desktop", "download",
            "open", "app", "system", "setting", "browser",
        )):
            tasks.append(SwarmTask(
                agent_type="system_ops",
                goal=f"Handle system operations for: {goal}",
                thread_id=thread_id,
            ))

        if len(tasks) <= 1:
            tasks.append(SwarmTask(
                agent_type="researcher",
                goal=f"Research: {goal}",
                thread_id=thread_id,
            ))

        if len(tasks) > 1:
            tasks.append(SwarmTask(
                agent_type="reviewer",
                goal=f"Review all outputs for: {goal}",
                thread_id=thread_id,
                depends_on=[
                    t.agent_type for t in tasks if t.agent_type != "reviewer"
                ],
            ))

        return SwarmPlan(tasks=tasks, review_needed=len(tasks) > 1)

    def run_swarm(
        self,
        goal: str,
        ctx: ExecutionContext | None = None,
        *,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> SwarmResult:
        """Execute a full swarm: decompose → dispatch → review → merge."""
        ctx = ctx or ExecutionContext()
        plan = self.decompose(goal)

        if on_progress:
            on_progress("orchestrator",
                        f"Decomposed into {len(plan.tasks)} tasks")

        swarm_result = SwarmResult()
        completed: dict[str, AgentResult] = {}
        pending: dict[str, SwarmTask] = {}

        for task in plan.tasks:
            pending[task.agent_type] = task

        while pending:
            ready = [
                t for t in pending.values()
                if all(dep in completed for dep in t.depends_on)
            ]
            if not ready and pending:
                ready = list(pending.values())

            if len(ready) == 1:
                task = ready[0]
                spec = SWARM_AGENTS.get(task.agent_type)
                if spec is None:
                    completed[task.agent_type] = AgentResult(
                        answer=f"Unknown agent: {task.agent_type}",
                        stopped_reason="error",
                    )
                    del pending[task.agent_type]
                    continue

                if on_progress:
                    on_progress(task.agent_type, "Starting...")
                result = run_swarm_agent(
                    spec, task.goal, ctx,
                    channel=self.channel,
                    thread_id=task.thread_id,
                )
                completed[task.agent_type] = result
                del pending[task.agent_type]
                if on_progress:
                    on_progress(task.agent_type,
                                f"Done ({result.stopped_reason})")
            else:
                def _run(t: SwarmTask) -> tuple[str, AgentResult]:
                    s = SWARM_AGENTS.get(t.agent_type)
                    if s is None:
                        return t.agent_type, AgentResult(
                            answer=f"Unknown: {t.agent_type}",
                            stopped_reason="error",
                        )
                    return t.agent_type, run_swarm_agent(
                        s, t.goal, ctx,
                        channel=self.channel,
                        thread_id=t.thread_id,
                    )

                max_w = min(4, len(ready))
                with ThreadPoolExecutor(max_workers=max_w) as pool:
                    futures = {pool.submit(_run, t): t for t in ready}
                    for fut in as_completed(futures):
                        try:
                            name, result = fut.result()
                            completed[name] = result
                            del pending[name]
                            if on_progress:
                                on_progress(name,
                                            f"Done ({result.stopped_reason})")
                        except Exception as exc:
                            task = futures[fut]
                            completed[task.agent_type] = AgentResult(
                                answer=f"Agent crashed: {exc}",
                                stopped_reason="error",
                            )
                            del pending[task.agent_type]

        swarm_result.results = completed
        swarm_result.conversation = self.channel.get_thread(
            plan.tasks[0].thread_id if plan.tasks else "",
        )
        swarm_result.success = all(
            r.stopped_reason == "done" for r in completed.values()
        )
        swarm_result.summary = _build_swarm_summary(completed, swarm_result)
        return swarm_result


def _build_swarm_summary(
    completed: dict[str, AgentResult],
    swarm_result: SwarmResult,
) -> str:
    """Build a human-readable summary of swarm results."""
    parts = ["## Swarm Execution Summary\n"]
    for name, result in completed.items():
        status = "✅" if result.stopped_reason == "done" else "⚠️"
        parts.append(
            f"{status} **{name}** ({len(result.steps)} steps): "
            f"{result.answer[:200]}"
        )

    if swarm_result.conversation:
        conv_count = len(swarm_result.conversation)
        parts.append(
            f"\n💬 **Agent conversations**: {conv_count} messages exchanged"
        )
        for intent in (Intent.ASK, Intent.ANSWER, Intent.DELEGATE):
            count = sum(
                1 for m in swarm_result.conversation
                if m.intent == intent
            )
            if count:
                parts.append(
                    f"  - {intent.value.capitalize()}: {count}"
                )

    return "\n".join(parts)


# ========================================================================= #
# Integration entry point                                                    #
# ========================================================================= #


def run_swarm_task(
    goal: str,
    ctx: ExecutionContext | None = None,
    *,
    on_progress: Callable[[str, str], None] | None = None,
) -> str:
    """Entry point — auto-selects swarm vs single agent.

    If the goal needs multiple capabilities, launches a full swarm.
    Otherwise falls back to a single autonomous agent.
    """
    swarm = SwarmOrchestrator()
    plan = swarm.decompose(goal)

    if len(plan.tasks) <= 1:
        spec = list(SWARM_AGENTS.values())[0]
        result = run_swarm_agent(spec, goal, ctx)
        return result.answer

    result = swarm.run_swarm(goal, ctx, on_progress=on_progress)
    return result.summary
