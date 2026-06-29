# OpenHarness Integration

The current integration boundary is documented in
[PROVIDER_INTEGRATION.md](PROVIDER_INTEGRATION.md).

SignalHarness originated from OpenHarness downstream work and keeps MIT
attribution, but the public package now ships an independent SignalHarness
core. Demo and `mock-agent` modes do not import or require the upstream
OpenHarness package.

The remaining OpenHarness references are optional integration hooks for
`--mode agent`. They are imported lazily only when the real-provider path is
selected. If that optional runtime is unavailable, SignalHarness reports a
clear error and suggests `demo` or `mock-agent` for offline execution.

ContextEvidenceAgent tool use is a controlled two-turn bridge over the existing
SignalToolExecutor and ToolRegistry. The model never executes tools directly.
The bridge records requests, executions, blocks, errors, and per-run cache hits.

Collection uses concurrent `asyncio` source tasks with per-source timeout,
local TTL cache, and partial-failure reporting. It intentionally does not add a
daemon, Celery, Redis, Postgres, embeddings, or a vector database.

Scripted mock evals run in CI without a real key. Real provider mode is a
manual smoke test and does not use provider-native function calling.
