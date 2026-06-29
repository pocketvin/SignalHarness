# Architecture Decisions

## Upstream dependency surface

SignalHarness is built on HKUDS/OpenHarness and keeps OpenHarness as its only
Agent Harness foundation. Some dependencies in `pyproject.toml` are inherited
from the upstream OpenHarness runtime, including provider clients, terminal UI,
MCP, messaging integrations, and frontend-related packages.

SignalHarness itself uses a focused subset of that surface for the public MVP:

- the OpenHarness-compatible provider adapter;
- the local CLI and trace/reporting path;
- schema validation, deterministic guardrails, and permission checks;
- offline `demo` and scripted `mock-agent` evaluation;
- a small read-only signal tool loop.

The heavier upstream capabilities remain available because they are part of the
OpenHarness base, but they are not presented as SignalHarness-specific runtime
requirements. Public CI therefore validates only SignalHarness-focused offline
checks and does not require real API keys, live provider calls, external
observability services, databases, queues, or vector stores.

This keeps the project honest for demo and interview purposes: SignalHarness
deeply integrates with OpenHarness, but its own design intentionally stays
narrow, local-first, and reviewable.
