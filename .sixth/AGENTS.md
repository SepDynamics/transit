# Agent Manifold — Project Instructions

This project is **agent-manifold**, a flight recorder and circuit breaker for agent execution traces.

## Automatic session collection

A background watcher (`scripts/am_watch.py`) monitors `~/.codex/sessions/` and automatically ingests Codex sessions for this project into `traces/codex-*.jsonl`.

Start the watcher if it is not already running:
```bash
pgrep -f am_watch.py || setsid -f python scripts/am_watch.py --historical-days 30 --poll > /tmp/am-watch.log 2>&1
```

## Kilo session coverage

Kilo loads the root `AGENTS.md` for this project, so this file is the project-level Kilo integration point. Kilo does not currently produce Codex-compatible session JSONL in this workspace, so use one of the explicit trace paths when instrumenting Kilo-authored loops:

```bash
python my_agent.py | agent-manifold collect kilo --session-id kilo-run --output traces/kilo-run.jsonl
```

For Python loops, prefer `TracedAgent` so Kilo-authored code gets tracing, budget enforcement, and live monitor state without needing a native Kilo session export.

## When you make changes in this project

After completing work that involves multiple tool calls (file edits, test runs, shell commands), run the validation pipeline to confirm the monitor is still working:

```bash
python -m pytest tests/ -q
agent-manifold summary traces/codex-*.jsonl 2>/dev/null | python -c "import sys,json; [print(json.dumps({k:v for k,v in json.loads(l).items() if k in ('session_id','event_count','final_label','outcomes','max_hazard')},sort_keys=True)) for l in sys.stdin]"
```

## Tracing your own tool loop (Python)

Use `TracedAgent` to add tracing + circuit breaking to any agent loop:

```python
from agent_manifold import TracedAgent, Budget

with TracedAgent("traces/my-run.jsonl", budget=Budget(max_tool_calls=40)) as agent:
    agent.on_alert("loop", lambda s: print(f"loop at event {s.event_index}"))
    agent.user_message(tokens_in=350)
    with agent.tool("bash", command=cmd) as ctx:
        result = run_bash(cmd)
    if agent.state.label in ("loop", "collapse_risk"):
        agent.abort("early_stop")
```

## Pipeline commands

```bash
agent-manifold summary   traces/*.jsonl          # per-session summaries
agent-manifold timeline  traces/*.jsonl          # label transition history
agent-manifold coverage  traces/*.jsonl          # vocabulary gap report
agent-manifold validate  traces/*.jsonl --early-detection
agent-manifold export-labels traces/*.jsonl     # inferred labels for review
```

## What the labels mean

- `warming` — not enough events for a structural window yet
- `stable` — normal progress
- `loop` — repeated tool cycle with no error progression
- `thrash` — high entropy + high hazard (edit/fail/retry pattern)
- `collapse_risk` — coherence collapse, high entropy, user waiting
- `recovery` — hazard dropping, coherence rising from a bad state
- `budget_exhausted` — hard limit hit (tokens, time, tool calls, or failures)
