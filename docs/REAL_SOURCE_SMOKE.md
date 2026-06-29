# Real Source Smoke Test

This manual smoke test verifies source collection against configured GitHub,
RSS, or fixture-backed web-change sources. It is separate from real provider
smoke testing and is not required for public CI.

Public CI uses fixture-safe SignalHarness checks only: `tests/signal_harness`,
Ruff, mypy, and `uv build`. No real network, API key, GitHub token, or RSS
availability is required for CI.

## Before running

Keep credentials out of the repository:

```bash
export GITHUB_TOKEN="..."   # optional, only if your GitHub source needs it
export LLM_API_KEY="..."    # only needed for --mode agent, not for demo/mock-agent
```

Do not commit `.env`, runtime state, outputs, caches, or hardcoded keys.

## Run with configured sources

Review `configs/watchlist.yaml`, then run:

```bash
uv run signal-harness scan --mode demo
uv run signal-harness trace
uv run signal-harness dashboard
```

For the real LLM provider path:

```bash
export LLM_PROVIDER="openai_compatible"
export LLM_MODEL_PROFILE="openai_gpt4o_mini"
export LLM_MODEL="gpt-4o-mini"
export LLM_API_KEY="..."

uv run signal-harness scan --mode agent
uv run signal-harness trace
```

## What to inspect

- `outputs/task_trace.json` for `SourceTask` records, source failures, cache
  events, and Agent trace.
- `outputs/trace_summary.md` for failed sources, skipped audit fallback, tool
  controls, and repair pass status.
- `outputs/dashboard.html` for source health, score breakdowns, repair pass,
  model/profile/limits, and learning staging.

Real source calls can fail because of network availability, rate limits,
credential scope, feed format, endpoint changes, or provider behavior. That is
why the project keeps fixture-driven demo/mock-agent CI as the public
acceptance path.

## Boundary

The source smoke test validates source adapters, cache/failure trace, schema
fallback, permission guardrails, and local reporting. It does not enable
external notifications, native provider tool calling, Redis/Postgres queues, or
workflow dashboards.
