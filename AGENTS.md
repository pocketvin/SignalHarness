# SignalHarness Agent Guide

SignalHarness currently contains a standalone LLM-enhanced routed multi-agent signal
intelligence implementation.

For future product direction and construction planning, `docs/SIGNALHARNESS_TARGET_STATE.md`
and `docs/SIGNALHARNESS_WORKING_PLAN.md` take precedence over older fixed-five-Agent
product assumptions. The existing five-Agent implementation remains a protected current-state
baseline until an evidence-based Analyzer/Harness migration is implemented and verified.

Key directories:

- `agent_team/`: five domain LLM Agents
- `agent_integration/`: prompts, schemas, context, runner, trace
- `providers/`: scripted mock adapter and optional provider integration
- `memory/`: Project, Signal, Feedback, and Policy infrastructure
- `signal/`: deterministic normalization, noise, clustering, scoring
- `runtime/`: workflow, cache, permissions, tools, trace

Run with:

```bash
uv run signal-harness scan --mode demo|mock-agent|agent
uv run signal-harness trace
uv run signal-harness calibrate --mode mock-agent
uv run signal-harness serve --host 127.0.0.1 --port 8000  # /demo + REST/SSE + /mcp
```

Rules:

- Do not add LangGraph, CrewAI, AutoGen, Redis, Postgres, Celery, VectorDB, or
  embedding databases.
- Do not describe deterministic fallback as true multi-Agent execution.
- Memory is infrastructure, not an Agent.
- `demo` is deterministic fallback; `mock-agent` uses scripted offline LLM-like
  calls; `agent` uses an optional real-provider integration.
- Keep provider adapters thin; demo, mock-agent, and public CI must not require
  upstream framework imports.
- Prompt, schema, route, or tool-use changes require matching tests and docs.
- Public CI must stay SignalHarness-focused and offline: pytest
  `tests/signal_harness`, Ruff, mypy, and `uv build` only. It must not require
  `LLM_API_KEY`, run `--mode agent`, or call live providers. Real API smoke
  tests are documented, manual, and require explicit environment variables.
- Never commit hardcoded API keys, secret-looking fallback credentials, `.env`,
  runtime outputs, caches, or build artifacts.

Before handoff:

```bash
uv run --extra dev python -m pytest tests/signal_harness -q
uv run --extra dev ruff check src/signal_harness tests/signal_harness
uv run --extra dev mypy src/signal_harness
uv run signal-harness scan --fixture examples/signal_harness/sample_events.json --mode mock-agent
uv run signal-harness trace
uv run signal-harness calibrate --mode mock-agent
uv build
```
