"""
Register all tools from declarations + action handlers.
Add a new capability: implement action, add to HANDLERS and TOOL_DECLARATIONS only.
"""

from __future__ import annotations

from typing import Any, Callable

from hybrid.guards import allow_screen_process
from hybrid.orchestrator import Orchestrator
from hybrid.registry import ToolRegistry
from hybrid.types import ExecutionContext

# Mirrors main._SLOW_TOOLS — used for UI progress only
_SLOW_TOOLS = frozenset({
    "web_search", "download_control", "youtube_video", "agent_task", "file_processor",
    "flight_finder", "screen_process",
    "send_email", "browser_control", "file_controller", "calendar_control",
    "notes_control", "organizer_control", "document_tools", "list_manager",
    "screen_act", "weather_report", "discuss_project", "search_docs",
})

# agent routing + categories
_TOOL_META: dict[str, dict[str, Any]] = {
    "open_app": {"agent": "system", "category": "system", "fast": True},
    "system_control": {"agent": "system", "category": "system", "fast": True},
    "computer_settings": {"agent": "system", "category": "system", "fast": True},
    "desktop_control": {"agent": "system", "category": "system", "fast": True},
    "computer_control": {"agent": "system", "category": "system", "fast": False},
    "file_controller": {"agent": "system", "category": "files", "fast": False},
    "file_processor": {"agent": "system", "category": "files", "fast": False},
    "organizer_control": {"agent": "system", "category": "files", "fast": False},
    "document_tools": {"agent": "system", "category": "files", "fast": False},
    "list_manager": {"agent": "system", "category": "files", "fast": False},
    "notes_control": {"agent": "system", "category": "productivity", "fast": False},
    "calendar_control": {"agent": "system", "category": "productivity", "fast": False},
    "reminder": {"agent": "system", "category": "productivity", "fast": False},
    "browser_control": {"agent": "system", "category": "browser", "fast": False},
    "web_search": {"agent": "research", "category": "research", "fast": False},
    "download_control": {"agent": "system", "category": "files", "fast": False},
    "youtube_video": {"agent": "research", "category": "research", "fast": False},
    "flight_finder": {"agent": "research", "category": "research", "fast": True},
    "screen_process": {"agent": "research", "category": "vision", "fast": False},
    "screen_act": {"agent": "research", "category": "vision", "fast": False},
    "weather_report": {"agent": "research", "category": "research", "fast": True},
    "send_message": {"agent": "system", "category": "comms", "fast": False},
    "send_email": {"agent": "system", "category": "comms", "fast": False},
    "contact_manager": {"agent": "memory", "category": "memory", "fast": False},
    "save_memory": {"agent": "memory", "category": "memory", "internal": True, "fast": False},
    "agent_task": {"agent": "tool", "category": "agent", "fast": False},
    "spawn_agent": {"agent": "tool", "category": "agent", "fast": False},
    "shutdown_aria": {"agent": "system", "category": "system", "internal": True, "fast": False},
    "memory_tool": {"agent": "memory", "category": "memory", "fast": True},
    "apply_skill": {"agent": "memory", "category": "memory", "fast": False},
    "screen_analyze": {"agent": "research", "category": "vision", "fast": False},
    "discuss_project": {"agent": "research", "category": "discussion", "fast": False},
    "search_docs": {"agent": "research", "category": "discussion", "fast": False},
    "exa_search": {"agent": "research", "category": "research", "fast": False},
}


def _wrap_action(
    fn: Callable,
    *,
    speak: bool = False,
    use_response: bool = False,
    use_session_memory: bool = False,
) -> Callable[[dict, ExecutionContext], str]:
    def handler(args: dict, ctx: ExecutionContext) -> str:
        kwargs: dict = {"parameters": args, "player": ctx.ui}
        if speak and ctx.speak:
            kwargs["speak"] = ctx.speak
        if use_response:
            kwargs["response"] = None
        if use_session_memory:
            kwargs["session_memory"] = ctx.session_memory
        out = fn(**kwargs)
        return out if out is not None else "Done."

    return handler


def _build_handlers() -> dict[str, Callable]:
    from actions.browser_control import browser_control
    from actions.calendar import calendar_control
    from actions.computer_control import computer_control
    from actions.computer_settings import computer_settings
    from actions.contacts import contact_manager
    from actions.desktop import desktop_control
    from actions.document_tools import document_tools
    from actions.file_controller import file_controller
    from actions.file_processor import file_processor
    from actions.flight_finder import flight_finder
    from actions.list_manager import list_manager
    from actions.notes import notes_control
    from actions.open_app import open_app
    from actions.organizer import organizer_control
    from actions.reminder import reminder
    from actions.send_email import send_email
    from actions.send_message import send_message
    from actions.system_control import system_control
    from actions.weather_report import weather_action
    from actions.download_control import download_control
    from actions.discuss_project import discuss_project
    from actions.search_docs import search_docs
    from actions.web_search import web_search as web_search_action
    from actions.youtube_video import youtube_video
    from actions.create_presentation import create_presentation
    from actions.screen_analyze import screen_analyze
    from actions.fast_fetch import fast_fetch
    from actions.exa_search import exa_search


    return {
        "open_app": _wrap_action(open_app, use_response=True, use_session_memory=True),
        "weather_report": _wrap_action(weather_action),
        "browser_control": _wrap_action(browser_control),
        "file_controller": _wrap_action(file_controller),
        "send_message": _wrap_action(send_message, use_response=True, use_session_memory=True),
        "send_email": _wrap_action(send_email),
        "contact_manager": _wrap_action(contact_manager),
        "calendar_control": _wrap_action(calendar_control),
        "reminder": _wrap_action(reminder, use_response=True),
        "notes_control": _wrap_action(notes_control),
        "organizer_control": _wrap_action(organizer_control),
        "document_tools": _wrap_action(document_tools),
        "list_manager": _wrap_action(list_manager),
        "youtube_video": _wrap_action(youtube_video, use_response=True),
        "system_control": _wrap_action(system_control),
        "computer_settings": _wrap_action(computer_settings, use_response=True),
        "desktop_control": _wrap_action(desktop_control),
        "agent_task": _agent_task_handler,
        "web_search": _wrap_action(web_search_action),
        "fast_fetch": _wrap_action(fast_fetch),
        "download_control": _wrap_action(download_control),
        "file_processor": _file_processor_handler,
        "computer_control": _wrap_action(computer_control),
        "flight_finder": _wrap_action(flight_finder),
        "create_presentation": _wrap_action(create_presentation),
        "spawn_agent": _spawn_agent_handler,
        "memory_tool": _memory_tool_handler,
        "apply_skill": _apply_skill_handler,
        "screen_analyze": _wrap_action(screen_analyze, use_response=True),
        "discuss_project": _wrap_action(discuss_project, speak=True, use_response=True),
        "search_docs": _wrap_action(search_docs, speak=True, use_response=True),
        "exa_search": _wrap_action(exa_search),
        "shutdown_aria": lambda _a, _c: "Goodbye.",
    }


def _memory_tool_handler(args: dict, ctx: ExecutionContext) -> str:
    from core.memory_rag import store_memory, retrieve_relevant_memory
    action = args.get("action", "")
    content = args.get("content", "")
    category = args.get("category", "general")
    
    if action == "store":
        if store_memory(category, content):
            return f"Successfully stored memory in category '{category}'"
        return "Failed to store memory."
    elif action == "retrieve":
        memories = retrieve_relevant_memory(content, top_k=3, category=category if category != "general" else None)
        if not memories:
            return "No relevant memories found."
        out = "Memories found:\n"
        for m in memories:
            out += f"- [{m['metadata'].get('category', 'general')}]: {m['content']}\n"
        return out
    return "Invalid action. Use 'store' or 'retrieve'."


def _apply_skill_handler(args: dict, ctx: ExecutionContext) -> str:
    from actions.skill_loader import apply_skill_by_name

    name = args.get("skill_name") or args.get("name") or ""
    query = args.get("query") or ""
    return apply_skill_by_name(name, query)


def _file_processor_handler(args: dict, ctx: ExecutionContext) -> str:
    from actions.file_processor import file_processor

    if not args.get("file_path") and ctx.ui and getattr(ctx.ui, "current_file", None):
        args = dict(args)
        args["file_path"] = ctx.ui.current_file
    return _wrap_action(file_processor, speak=True)(args, ctx)


def _save_memory_handler(args: dict, ctx: ExecutionContext) -> str:
    from core.memory_ext import store_memory_smart

    category = args.get("category", "notes")
    key = args.get("key", "")
    value = args.get("value", "")
    if key and value:
        stored = store_memory_smart(category, value)
        if stored:
            print(f"[Memory] 💾 {category}/{key} = {value[:60]}")
    return "ok"


def _is_complex_goal(goal: str) -> bool:
    """Return True if the goal needs multiple agent types (→ swarm)."""
    keywords = (
        "research and", "compare and",
        "both", "multiple", "also",
    )
    return any(kw in goal.lower() for kw in keywords)


def _agent_task_handler(args: dict, ctx: ExecutionContext) -> str:
    goal = (args.get("goal") or "").strip()
    if not goal:
        return "No goal provided for agent_task."

    # Autonomous mode (opt-in): the model reasons over real tool results in a loop
    # instead of pre-planning fixed steps. Falls back to the planner on any error.
    try:
        from config import get_config

        if get_config().get("autonomous_mode", True):
            # Try GoalDispatcher first — splits multi-task input into parallel goals.
            # Falls back to swarm for complex single goals, then single agent.
            from core.goal_dispatcher import get_dispatcher, split_goals

            goals = split_goals(goal)
            if len(goals) >= 2:
                print(f"[GoalDispatcher] Detected {len(goals)} independent goals, dispatching in parallel")
                result = get_dispatcher().dispatch(goal, ctx)
                return result.summary

            if _is_complex_goal(goal):
                from core.agent_swarm import run_swarm_task

                return run_swarm_task(goal, ctx)
            from core.agent_loop import run_agent

            result = run_agent(
                goal, ctx,
                on_step=lambda s: print(f"[agent] {s.tool}({s.args}) -> {s.result[:80]}"),
            )
            return result.answer
    except Exception as e:
        print(f"[agent_task] autonomous mode error, using planner fallback: {e}")

    orch = ctx.get("orchestrator")
    if orch and hasattr(orch, "run_planned_sync"):
        return orch.run_planned_sync(goal, ctx)
    # No task queue available — fall back to telling the user the goal was noted
    return f"Goal received: '{goal}'. I'll work on it."


def _spawn_agent_handler(args: dict, ctx: ExecutionContext) -> str:
    agent_type = args.get("agent_type")
    goal = args.get("goal")
    if not agent_type or not goal:
        return "Error: agent_type and goal are required."
    
    from core.sub_agents import BUILT_IN_AGENTS, run_sub_agent
    
    spec = BUILT_IN_AGENTS.get(agent_type)
    if not spec:
        return f"Error: Unknown agent type '{agent_type}'. Available: {', '.join(BUILT_IN_AGENTS.keys())}"
        
    try:
        result = run_sub_agent(spec, goal, ctx, on_step=lambda s: print(f"[SubAgent:{agent_type}] {s.tool}() -> {s.ok}"))
        return f"Sub-agent '{agent_type}' finished. Result:\n{result.answer}"
    except Exception as e:
        return f"Sub-agent '{agent_type}' crashed: {e}"


def register_all_tools(registry: ToolRegistry | None = None) -> ToolRegistry:
    registry = registry or ToolRegistry.instance()
    handlers = _build_handlers()

    # Dynamic Tool Discovery: Auto-load any new actions in the actions/ folder
    import importlib.util
    from pathlib import Path
    
    actions_dir = Path(__file__).parent.parent / "actions"
    if actions_dir.exists():
        for py_file in actions_dir.glob("*.py"):
            if py_file.name.startswith("_") or py_file.name == "dev_run.py":
                continue
            
            module_name = py_file.stem
            if module_name not in handlers:
                try:
                    spec = importlib.util.spec_from_file_location(module_name, py_file)
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        if hasattr(mod, "TOOL_DECLARATION") and hasattr(mod, module_name):
                            decl = mod.TOOL_DECLARATION
                            # Append to declarations if not already there
                            if decl not in TOOL_DECLARATIONS:
                                TOOL_DECLARATIONS.append(decl)
                            handlers[module_name] = _wrap_action(getattr(mod, module_name))
                            print(f"[ToolRegistry] Dynamically loaded {module_name}")
                except Exception as e:
                    print(f"[ToolRegistry] Failed to dynamic load {module_name}: {e}")

    # Declarative fast-path patterns — moved here from router.py
    # To add a new fast-path tool: add patterns here, not in router.py.
    _FAST_PATH_PATTERNS: dict[str, list[tuple[str, dict]]] = {
        "system_control": [
            (r"(?:increase|turn up|raise)\s+(?:the\s+)?volume|volume up|louder",
             {"action": "volume", "direction": "up"}),
            (r"(?:decrease|turn down|lower)\s+(?:the\s+)?volume|volume down|quieter",
             {"action": "volume", "direction": "down"}),
            (r"(?:mute|silence)\s+(?:the\s+)?(?:volume|sound)|\bmute\b",
             {"action": "volume", "direction": "mute"}),
            (r"(?:increase|turn up|raise)\s+(?:the\s+)?brightness|brighter",
             {"action": "brightness", "direction": "up"}),
            (r"(?:decrease|turn down|lower|dim)\s+(?:the\s+)?brightness|dimmer",
             {"action": "brightness", "direction": "down"}),
        ],
    }

    for name, handler in handlers.items():
        if name == "save_memory":
            handler = _save_memory_handler
        meta = _TOOL_META.get(name, {})
        guard = None
        if name == "screen_process":
            guard = allow_screen_process

        registry.register(
            name=name,
            description=meta.get("description", name),
            parameters={},
            handler=handler,
            category=meta.get("category", "general"),
            agent=meta.get("agent", "tool"),
            fast_eligible=meta.get("fast", True),
            slow=name in _SLOW_TOOLS,
            internal=meta.get("internal", False),
            guard=guard,
            fast_path_patterns=_FAST_PATH_PATTERNS.get(name),
        )

    from actions.skill_loader import load_all_skills
    load_all_skills()

    print(f"[ToolRegistry] Registered {len(registry.names())} tools")
    return registry


_orchestrator: Orchestrator | None = None


def init_hybrid_system() -> Orchestrator:
    global _orchestrator
    registry = register_all_tools()
    _orchestrator = Orchestrator(registry=registry)
    
    from hybrid.observer import ContinuousLearningObserver
    from hybrid.task_bus import get_task_bus
    _observer = ContinuousLearningObserver(get_task_bus())
    
    return _orchestrator


def get_orchestrator() -> Orchestrator:
    if _orchestrator is None:
        return init_hybrid_system()
    return _orchestrator
