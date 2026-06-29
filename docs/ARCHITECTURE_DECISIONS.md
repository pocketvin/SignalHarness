# Architecture Decisions

SignalHarness is an OpenHarness downstream project: OpenHarness is the only
Agent Harness base, and SignalHarness adds a focused signal-intelligence layer
on top rather than replacing the upstream runtime.

## Project position

SignalHarness is best described as an LLM-enhanced routed multi-agent signal
intelligence harness. It uses five structured Agents, a controlled two-step
tool-use loop, deterministic guardrails, local trace/eval artifacts, and
review-only learning proposals.

It is intentionally not presented as:

- a fully autonomous self-evolving Agent system;
- a provider-native function calling system;
- a fully conversational multi-Agent debate system;
- a LangGraph, CrewAI, AutoGen, Dify, or Haystack orchestration clone.

## Why OpenHarness remains the only Agent Harness base

SignalHarness reuses the OpenHarness provider surface, tool registry concepts,
streaming client path, CLI packaging, and runtime conventions. Adding a second
Agent framework would make the demo harder to explain and would blur the
project boundary: the goal is to show a downstream OpenHarness application, not
an orchestration-framework comparison.

Some heavier dependencies in `pyproject.toml` mostly come from upstream
OpenHarness, including provider clients, terminal UI, MCP, messaging
integrations, and frontend-related packages. SignalHarness itself uses a
focused subset:

- the OpenHarness-compatible provider adapter;
- the local CLI and trace/reporting path;
- schema validation, deterministic guardrails, and permission checks;
- offline `demo` and scripted `mock-agent` evaluation;
- a small read-only signal tool loop.

Public CI therefore validates only SignalHarness-focused offline checks and
does not require real API keys, live provider calls, external observability
services, databases, queues, or vector stores.

## Why not LangGraph, CrewAI, AutoGen, or Dify

Those projects are useful references, but introducing them would add another
runtime model, another dependency surface, and another explanation burden.
SignalHarness borrows ideas such as explicit routing, schema-first outputs,
bounded tool use, and local trace/eval summaries, while keeping execution
inside the OpenHarness downstream project.

## Controlled two-step tool-use loop

`ContextEvidenceAgent` uses one planned tool phase and one final evidence phase:

1. the Agent returns `EvidenceToolPlan`;
2. Python validates schema, tool allowlist, permission policy, and tool budget;
3. Python executes allowed read-only tools through `SignalToolExecutor`;
4. the Agent receives `ToolObservation` objects and returns final evidence.

This is not provider-native function calling. The provider is still asked for a
structured JSON response, and the Python runner owns tool execution. The loop is
also not a complex multi-round autonomous loop. That is deliberate: the current
goal is stable, testable, explainable behavior. Richer autonomous looping is
future work.

## Why memory is infrastructure, not an Agent

`ProjectMemory`, `SignalMemory`, `FeedbackMemory`, and `PolicyMemory` are local
state stores. They are read by Agents, especially `LearningPolicyAgent`, but
they do not decide routes, execute tools, or mutate policy by themselves.
Treating memory as infrastructure keeps state ownership explicit.

## Review-only learning proposals

`LearningPolicyAgent` produces policy, skill, and watchlist proposals for human
review. SignalHarness saves these artifacts, including
`latest_learning_observation.json`, but does not automatically apply policy
changes, edit watchlists, or modify skill files. Applying a policy remains a
separate explicit approval path.

## Deterministic scorer remains authoritative

LLM Agents provide semantic relevance, evidence confidence, impact reasoning,
and suggested actions. They do not own `final_score`. Python keeps the guarded
scorer because it is deterministic, testable, and policy-controlled:

- deterministic scoring supplies the baseline;
- LLM semantic relevance and evidence confidence are bounded inputs;
- policy weights and multipliers are applied in Python;
- schema violations or missing coverage fall back to deterministic defaults.

## Current limitations

- The real Agent mode still depends on structured JSON responses, now with one
  schema retry before deterministic fallback.
- Tool use is controlled by the runner, not provider-native function calling.
- The evidence loop is bounded to two steps and does not implement autonomous
  multi-round tool exploration.
- The system is not fully autonomous or self-evolving.
- It is not a conversational multi-Agent debate runtime.
- Clustering is lightweight and rule-based, not embedding or vector-search
  based.

## Future work

- Provider-native or OpenHarness-native tool calling.
- Richer memory retrieval.
- Optional multi-round evidence loop.
- Stricter real-source evaluation.
- Independent `signalharness-core` package.

This keeps the project honest for demo and interview purposes: SignalHarness
deeply integrates with OpenHarness, but its own design intentionally stays
narrow, local-first, and reviewable.
