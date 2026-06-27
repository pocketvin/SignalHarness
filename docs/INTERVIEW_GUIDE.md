# SignalHarness Interview Guide

## Thirty-second explanation

SignalHarness is an LLM-native multi-agent signal intelligence harness built on
OpenHarness. Five LLM Agents classify and route signals, verify evidence,
analyze project impact, plan bounded actions, and learn from memory. Python is
the constraint layer for schemas, fallback, permission, scoring, replay, and
traceability; it is not presented as the primary intelligence source.

## The three modes

- `demo` is the deterministic fallback for CI and offline demonstrations. It is
  not true multi-Agent execution.
- `mock-agent` uses an offline mock provider but exercises the real five-Agent
  architecture with scripted LLM-like JSON and controlled tool turns.
- `agent` uses a real OpenHarness-backed provider and is the primary project
  form.

## Public CI and real API tests

Public CI is intentionally offline and SignalHarness-focused: it runs scripted
`mock-agent` evals, trace generation, calibration, package build, and quality
checks scoped to `src/signal_harness` and `tests/signal_harness`. It does not
need a real API key.

Real provider tests are manual smoke tests under
`tests/manual/integration_real_api/`. They are excluded from default pytest and
read credentials only from environment variables. Hardcoded keys and fallback
credentials are forbidden.

## Why the score remains guarded

`ImpactAnalystAgent` supplies semantic relevance but cannot emit `final_score`.
Python combines the deterministic base, semantic relevance, evidence
confidence, and policy weights. Invalid Agent JSON or schema violations trigger
fallback. High-risk action requests are rechecked by `SignalPermissionGuard`.

## Memory and learning

Memory is infrastructure, not an Agent. `ProjectMemory`, `SignalMemory`,
`FeedbackMemory`, and `PolicyMemory` feed `LearningPolicyAgent`. It creates
policy, skill, and watchlist proposals plus a deterministic replay evaluation.
Nothing is applied without explicit approval.

## OpenHarness reuse

The real adapter calls OpenHarness's existing OpenAI-compatible streaming
client and message protocol. SignalHarness does not add a second provider
runtime or rewrite the OpenHarness Agent executor.

## Multi-step evidence reasoning

ContextEvidenceAgent first proposes read-only tool requests. Python validates
the allowlist and permissions, executes existing OpenHarness tools, and returns
observations to a second evidence turn. Failed tools do not crash the scan:
uncertainty increases and confidence is capped. Impact analysis then combines
evidence, project context, and lightweight multi-source clusters.

This remains a controlled runner loop, not provider-native function calling.
It also avoids implementing a complete handoff-as-tool protocol.

## Skipped routes and audit defaults

Supervisor routing controls actual downstream LLM execution. When a route
skips an event or stage, deterministic fallback can still populate the stored
assessment so every input has a complete audit record. That fallback is an
audit default, not evidence that the skipped downstream Agent ran.

## Engineering choices

Prompt prefixes keep static instructions and stable project context first;
events, tool observations, failures, and timestamps are later. Hashes make this
visible without provider-specific cache APIs. Source collection remains
concurrent and synchronous from the CLI perspective, with one observable
`SourceTask` per source and no background queue.

OpenHarness is the only Agent Harness dependency. Avoiding orchestration
frameworks, databases, queues, embeddings, and vector stores is a deliberate
MVP choice, not an omission hidden by the architecture diagram.

## Ideas borrowed without adding frameworks

SignalHarness borrows selected ideas from mature Agent projects without adding
their frameworks as dependencies:

- LangGraph and OpenAI Agents SDK: explainable supervisor routing and handoff
  concepts, without full handoff-as-tool.
- Haystack: bounded tool-loop ideas, implemented as a controlled tool-use loop.
- Langfuse: trace, eval, and prompt-version concepts, implemented with local
  trace files and eval summaries only.
- Dify and LlamaIndex: layered context and knowledge organization, without
  Dify or a general-purpose RAG platform.
- DSPy and Pydantic AI: schema-first Agent contracts, without adding either
  dependency.

## Demo script

```bash
uv run signal-harness scan \
  --fixture examples/signal_harness/sample_events.json \
  --mode mock-agent
uv run signal-harness report
uv run signal-harness trace
uv run signal-harness feedback \
  --signal-id demo-001 \
  --label useful \
  --note "checkpoint and memory signals are important"
uv run signal-harness calibrate --mode mock-agent
```

## Honest limitations

The OpenHarness-backed adapter currently targets OpenAI-compatible chat
endpoints through `LLM_API_KEY`, `LLM_MODEL`, and optional `LLM_BASE_URL`.
Evidence Agents receive collected primary-source context and can declare tool
requests, but broad live search is not enabled in the restricted SignalHarness
tool registry. The controlled tool loop is narrower than OpenHarness's complete
general Agent loop. Source clustering is rule-based rather than semantic.
Proposals are deliberately review-only.
