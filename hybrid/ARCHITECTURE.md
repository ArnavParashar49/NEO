# NEO hybrid-agent architecture

## Design principle

NEO uses adaptive routing. Simple device controls should not pay the cost of
planning, while multi-step goals retain explicit planning and verification.

| Path | Used for | Extra LLM calls |
|---|---|---:|
| Fast | Regex-matched volume and brightness commands | 0 |
| Direct | A Gemini Live function call to one registered tool | 0 |
| Planned | Multi-step or multi-goal work | 1 planner call, plus execution as needed |

## Runtime flow

```text
Voice or text input
  -> main.NeoLive
  -> hybrid.Orchestrator
  -> hybrid.ToolRegistry
  -> specialized agent
  -> actions/* handler
  -> ToolResult
```

Fast typed commands enter through `Orchestrator.try_fast_path()`. Gemini Live
function calls enter through `Orchestrator.execute_tool_for_live()`. Complex
goals enter through `Orchestrator.run_planned_sync()` and may be split by the
goal dispatcher.

## Components

| Module | Responsibility |
|---|---|
| `hybrid/orchestrator.py` | Routing, agent selection, planning and execution |
| `hybrid/registry.py` | Canonical tool schemas, metadata, guards and handlers |
| `hybrid/bootstrap.py` | Built-in action registration, dynamic discovery and MCP registration |
| `hybrid/router.py` | Zero-LLM fast-path classification |
| `hybrid/task_bus.py` | Structured events shared by agents and observers |
| `hybrid/types.py` | Tasks, routes, execution context and tool results |
| `hybrid/agents/*` | Thin role-specific wrappers around the registry |
| `core/agent_loop.py` | Bounded autonomous tool loop |
| `core/goal_dispatcher.py` | Independent-goal splitting and parallel dispatch |
| `core/audio_pipeline.py` | Shared audio primitives and extracted audio pipeline |
| `core/session_manager.py` | Shared transcript and session-state primitives |
| `core/tool_runner.py` | Shared tool execution helpers and recovery integration |

`main.py` still owns Gemini Live-specific lifecycle and UI coordination. Shared,
independently testable primitives belong in `core`; live-only behavior stays in
`NeoLive` until an extracted component has behavioral parity and integration
tests.

## Adding a built-in tool

1. Implement the action handler under `actions/`.
2. Add the import and mapping in `hybrid/bootstrap.py::_build_handlers`.
3. Add its schema and routing metadata to `_TOOL_META`.
4. Add a guard or fast-path pattern only when needed.
5. Add focused tests for the action and registry entry.

There is no `hybrid/declarations.py`. Gemini function declarations are generated
from `ToolRegistry.to_gemini_declarations()`.

## Dynamic action discovery

An action module not present in the built-in handler map can export:

```python
TOOL_DECLARATION = {
    "name": "my_tool",
    "description": "What the tool does",
    "parameters": {"type": "OBJECT", "properties": {}},
}

def my_tool(parameters, player=None):
    return "Done"
```

The declaration name must match the module and function name. Dynamic metadata
is copied into the registry during bootstrap.

## Private runtime data

The entire `memory/` directory, `.env`, `mcp.json`, and generated MCP caches are
machine-local. They must never be committed. Example configuration belongs in
`.env.example` and `config/api_keys.example.json`.
