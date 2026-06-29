# Architecture Decisions

SignalHarness started as OpenHarness downstream work, but the current goal is
an independent SignalHarness project: a standalone LLM-enhanced routed
multi-agent signal intelligence harness for GitHub/RSS/web-change signals.

## Public identity

The repository is not presented as an OpenHarness distribution. The public
identity is SignalHarness:

- five structured Agents for routing, evidence, impact, action, and learning;
- a controlled two-step evidence tool-use loop;
- deterministic guardrails for permissions, budgets, schema validation,
  fallback, trace, output, and final scoring;
- review-only learning proposals.

OpenHarness remains important for origin, attribution, and optional integration
ideas, but it is no longer packaged as the core runtime.

## Why the OpenHarness/oh/ohmo entry points were removed

The `openharness`, `oh`, `openh`, and `ohmo` public scripts made the project
look like an OpenHarness release. They were removed so interviewers and users
see one product surface:

```bash
signal-harness
```

The wheel now packages only `src/signal_harness`. Upstream directories such as
`src/openharness`, `ohmo`, the React terminal frontend, autopilot dashboard,
plugin folders, and upstream tests are not part of SignalHarness core.

## Local compatibility layer

SignalHarness keeps small local interfaces for what it actually needs:

- `signal_harness.utils.fs.atomic_write_text`
- `signal_harness.runtime.tools_base`

This lets `demo` and `mock-agent` run without importing the full OpenHarness
package. The default real-provider path is now a SignalHarness-native
OpenAI-compatible HTTP adapter using `httpx`. The OpenHarness adapter remains
available only behind `LLM_PROVIDER=openharness`.

## Why not LangGraph, CrewAI, AutoGen, LangChain, Dify, or similar frameworks

The workflow is a fixed routed pipeline rather than a dynamic graph runtime.
The engineering challenge is not framework orchestration; it is controlling
failure cost:

- schema retry and fallback;
- tool allowlists, permissions, and budgets;
- deterministic final scoring;
- local trace/eval visibility;
- review-only learning proposals.

Adding a full Agent framework would increase dependency surface and interview
explanation cost without making those core boundaries clearer.

## Why keep a custom `LLMAgentTeamRunner`

The runner is intentionally small and explicit. It makes the important Agent
engineering choices visible:

- which Agent runs at each stage;
- how tool requests are validated and executed;
- how invalid JSON/schema output is retried once;
- how provider timeouts are bounded by `AgentLoopLimits`;
- when deterministic fallback is used;
- why the LLM cannot directly execute tools or own final score.

This is easier to test, mock, and explain than hiding the policy inside a
general orchestration framework.

## Tool-use boundary

`ContextEvidenceAgent` uses a controlled two-step loop:

1. Agent returns `EvidenceToolPlan`.
2. Python validates schema, allowlist, permission policy, and budget.
3. Python executes allowed read-only tools.
4. Agent receives `ToolObservation` objects and returns final evidence.

This is not provider-native function calling. The provider returns structured
JSON; Python owns tool execution.

`ModelProfile` records conservative provider capabilities and strategy names:
`prompt_json_retry` for schemas and `controlled_tool_request` for tools.
Profiles are documentation and selection metadata, not permission to use
native tool calling.

## Eval boundary

`signal-harness model-eval` computes local metrics such as schema valid rate,
retry/fallback rate, timeout count, tool budget blocks, blocked tools, tool
errors, decision counts, repair counts, run-state isolation mode, and average
latency. It is intentionally local and does not depend on Langfuse, Ragas, or
any external eval platform.

## Repair boundary

Bounded repair is a limited design direction, not an open-ended ReAct loop.
The runner supports only Impact→ContextEvidence and Action→Impact repair.
It enforces repair round and event caps, reuses the same tool budget, does not
reset budgets for repair, and does not allow recursive repair. LearningPolicyAgent
cannot repair upstream Agents and cannot apply policy, watchlist, permission, or
skill changes automatically.

## Learning boundary

`LearningPolicyAgent` produces policy, skill, and watchlist proposals for human
review. SignalHarness saves these artifacts, including
`latest_learning_observation.json`, but does not automatically apply policy
changes, edit watchlists, or modify skills.

Learning staging adds a deterministic risk classifier and replay gate before
any explicit apply. The state source of truth is
`.signal-harness/learning_staging.json`; `outputs/latest_learning_staging.json`
and `outputs/latest_learning_risk_report.md` are demo snapshots only.

## Dependency boundary

Some heavy dependency patterns in the repository history came from upstream
OpenHarness breadth. SignalHarness uses a focused subset: Pydantic schemas,
Typer CLI, Rich terminal output, PyYAML configuration, `httpx` provider/source
adapters, and dev-only test/type/lint tooling. It deliberately does not add
LangGraph, CrewAI, AutoGen, LangChain, Dify, Haystack, Langfuse, Redis,
Postgres, Celery, vector databases, or external observability platforms.

## Deterministic scorer remains authoritative

LLM Agents provide semantic relevance, evidence confidence, impact reasoning,
and suggested actions. They do not own `final_score`. Python keeps final score
deterministic and policy-controlled.

## Current limitations

- Agent mode still depends on structured JSON responses, with one retry before
  deterministic fallback.
- Bounded repair is deliberately limited to two directions and small event sets.
- Tool use is runner-controlled, not provider-native function calling.
- The evidence loop is bounded to two steps.
- The system is not fully autonomous or self-evolving.
- It is not a conversational multi-Agent debate runtime.
- Clustering is lightweight and rule-based, not embedding/vector-search based.

## Long-term direction

- Independent SignalHarness core.
- Lightweight operational layer around the scan engine.
- Real-source smoke documentation.
- Optional provider integrations only when necessary.
- No framework adapter unless the workflow genuinely needs a more complex
  state model.
