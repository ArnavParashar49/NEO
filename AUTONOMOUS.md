# Autonomous mode

ARIA can run multi-step goals as an **autonomous tool-use loop** instead of a
pre-planned, fixed list of steps. The model decides one action at a time, sees the
**real** result, and decides the next action — until the goal is met.

## Enable / disable

It's **on by default**. To fall back to the classic planner, set this in
`config/api_keys.json`:

```json
"autonomous_mode": false
```

The flag is read at tool-call time, and any error in the loop falls back to the
planner automatically.

## How it works

```
goal + live tool schemas
        │
        ▼
  ┌─────────────────────────────┐
  │  model picks the next tool  │◀──────────────┐
  └──────────────┬──────────────┘               │
                 ▼                               │
        run tool via registry            feed the real result back
                 │                               │
                 ▼                               │
        goal met? ── no ─────────────────────────┘
                 │
                yes → short spoken summary
```

- The reasoning loop lives in [`core/agent_loop.py`](core/agent_loop.py) (`run_agent`).
- Tools and their schemas come from the registry (`registry.to_gemini_declarations()`),
  so there's **no hand-maintained tool list** to keep in sync.
- It's wired into the `agent_task` tool in
  [`hybrid/bootstrap.py`](hybrid/bootstrap.py); if anything fails it falls back to the
  planner automatically.

## What changed vs. the old planner

| Old (rigid planner) | New (autonomous loop) |
|---|---|
| Pre-writes a fixed list of steps before seeing any result | Decides each step from the **actual** previous result |
| "Steps are independent — never use previous results" | Results flow forward; later steps build on earlier ones |
| Hard cap of 8 steps | Step **budget** (default 12), tunable |
| Hand-written tool catalog in the prompt | Tool schemas straight from the registry |

The regex fast-path in [`hybrid/router.py`](hybrid/router.py) was trimmed to only a
handful of latency-critical system controls (volume, brightness, mute). Everything
else now defers to the model.

## Guardrails (structural, not just prompt text)

- **Step budget** — the loop can't run forever; on limit it summarizes where it landed.
- **Thrash guard** — the same tool+args called 3× aborts the loop.
- **Human-in-the-loop** — if a tool returns `NEEDS_CONFIRM` / `NEEDS_USER`, the loop
  **stops** and surfaces the question. It never auto-confirms destructive actions.
- **Audit log** — confirmed destructive actions are recorded in `cache/action_audit.log`.

## Going further

This makes **multi-step** requests autonomous. Single-turn conversational requests
already use Gemini Live's native function-calling. To make *every* utterance flow
through the loop, route the live path's unmatched intents into `run_agent` too — a
larger change to `main.py`, best done as its own step.

