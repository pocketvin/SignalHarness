# Provider Integration

SignalHarness core does not require a live provider. The supported default
paths are:

- `demo`: deterministic fallback, no LLM provider;
- `mock-agent`: scripted offline provider, no API key;
- `agent`: optional real-provider integration.

`MockProvider` implements the same tiny `AgentProvider` protocol used by the
runner, so offline evals exercise the real five-Agent routed path without
network access.

`OpenHarnessProvider` is kept as an optional compatibility adapter. It is not
imported by demo or mock-agent mode. If `--mode agent` is used without the
optional upstream provider runtime, SignalHarness returns a clear error and
suggests `demo` or `mock-agent`.

The real-provider path still asks for structured JSON. SignalHarness does not
use provider-native function calling: the model returns plans and outputs,
while Python validates schemas, executes read-only tools, applies budgets,
records trace, and computes final scores.

No `src/signal_harness/llm/` runtime exists. The deterministic core owns
normalization, deduplication, permissions, scoring constraints, persistence,
replay, reporting, and fallback.

