# OpenHarness Integration

The current integration boundary is documented in
[PROVIDER_INTEGRATION.md](PROVIDER_INTEGRATION.md).

SignalHarness reuses OpenHarness model clients, message protocol, tools, skills,
plugin agents, permissions patterns, atomic storage, and test conventions. Its
provider directory is a thin adapter, not a replacement runtime.

ContextEvidenceAgent tool use is a controlled two-turn bridge over the existing
SignalToolExecutor and ToolRegistry. The model never executes tools directly.
The bridge records requests, executions, blocks, errors, and per-run cache hits.

Collection uses concurrent `asyncio` source tasks with per-source timeout,
local TTL cache, and partial-failure reporting. It intentionally does not add a
daemon, Celery, Redis, Postgres, embeddings, or a vector database.

Scripted mock evals run in CI without a real key. Real provider mode is a manual
smoke test.
