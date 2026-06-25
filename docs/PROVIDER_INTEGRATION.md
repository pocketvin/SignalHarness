# Provider Integration

OpenHarness is the model-call foundation. SignalHarness does not implement a
parallel LLM runtime.

`OpenHarnessProvider` reuses:

- `openharness.api.openai_client.OpenAICompatibleClient`
- `openharness.api.client.ApiMessageRequest`
- `openharness.api.client.ApiMessageCompleteEvent`
- `openharness.engine.messages.ConversationMessage`

This preserves OpenHarness streaming, retries, provider errors, and message
conversion. The SignalHarness adapter only translates one domain `AgentCall`
into that existing protocol and collects final assistant text.

`MockProvider` implements the same tiny adapter for offline architecture tests.
Its default `scripted` strategy generates LLM-like JSON without calling Agent
fallback methods. The optional `fallback` strategy exists only for compatibility
tests. Invalid JSON or schema coverage failures trigger deterministic fallback
inside the runner.

`agent_team/` owns domain responsibilities and prompts. `agent_integration/`
owns schemas, mode selection, orchestration, and trace. The deterministic core
owns normalization, deduplication, scoring constraints, permissions,
persistence, replay, reporting, and fallback.

No `src/signal_harness/llm/` exists, and the OpenHarness query engine is not
rewritten.

The evidence tool loop is currently controlled by the SignalHarness runner:
the model proposes requests, while Python performs allowlist, permission,
execution, cache, and observation handling with the existing OpenHarness tool
registry. This is an explicit transition design; it does not recreate a
general-purpose tool runtime.

OpenHarness remains the sole Agent Harness base. LangGraph, CrewAI, and AutoGen
are not runtime dependencies. No Redis, Postgres, Celery, VectorDB, or embedding
store is required.
