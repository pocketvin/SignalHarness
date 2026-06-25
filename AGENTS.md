# SignalHarness Agent Guide

SignalHarness is an OpenHarness-based LLM-native signal intelligence harness.
Keep the fixed five-Agent architecture under `src/signal_harness/agent_team/`.

Key directories:

- `agent_team/`: five domain LLM Agents
- `agent_integration/`: prompts, schemas, context, runner, trace
- `providers/`: thin OpenHarness and scripted mock adapters
- `memory/`: Project, Signal, Feedback, and Policy infrastructure
- `signal/`: deterministic normalization, noise, clustering, scoring
- `runtime/`: workflow, cache, permissions, tools, trace

Run with:

```bash
uv run signal-harness scan --mode demo|mock-agent|agent
uv run signal-harness trace
uv run signal-harness calibrate --mode mock-agent
```

Rules:

- Do not add LangGraph, CrewAI, AutoGen, Redis, Postgres, Celery, VectorDB, or
  embedding databases.
- Do not describe deterministic fallback as true multi-Agent execution.
- Memory is infrastructure, not an Agent.
- `demo` is deterministic fallback; `mock-agent` uses scripted offline LLM-like
  calls; `agent` uses a real OpenHarness-backed provider.
- Keep provider adapters thin and use the existing OpenHarness tool registry
  and permission boundaries.
- Prompt, schema, route, or tool-use changes require matching tests and docs.

Before handoff:

```bash
uv run --extra dev python -m pytest tests/signal_harness -q
uv run --extra dev python -m pytest -q
uv run --extra dev ruff check src/signal_harness tests/signal_harness
uv run --extra dev mypy src/signal_harness
uv build
```
