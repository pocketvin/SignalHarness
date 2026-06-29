# SignalHarness Agent Guide

SignalHarness is a standalone LLM-enhanced routed multi-agent signal
intelligence harness.
Keep the fixed five-Agent architecture under `src/signal_harness/agent_team/`.

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
```

Rules:

- Do not add LangGraph, CrewAI, AutoGen, Redis, Postgres, Celery, VectorDB, or
  embedding databases.
- Do not describe deterministic fallback as true multi-Agent execution.
- Memory is infrastructure, not an Agent.
- `demo` is deterministic fallback; `mock-agent` uses scripted offline LLM-like
  calls; `agent` uses an optional real-provider integration.
- Keep provider adapters thin; demo and mock-agent must not require upstream
  OpenHarness imports.
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
