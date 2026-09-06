# SignalHarness Interview Guide

## Thirty-second explanation

SignalHarness is a standalone LLM-enhanced routed multi-agent signal
intelligence harness. Five structured Agents classify and route signals, verify
evidence, analyze project impact, plan bounded actions, and produce review-only
learning proposals. Python is the constraint layer for schemas, fallback,
permission, scoring, replay, and traceability.

## The three modes

- `demo` is the deterministic fallback for CI and offline demonstrations. It is
  not true multi-Agent execution.
- `mock-agent` uses an offline mock provider but exercises the real five-Agent
  architecture with scripted LLM-like JSON and controlled tool turns.
- `agent` uses the SignalHarness OpenAI-compatible provider by default and
  validates the same structured JSON schemas, with one schema retry before
  deterministic fallback.

## Public CI and real API tests

Public CI is intentionally offline and SignalHarness-focused: it runs
`python -m pytest tests/signal_harness -q`, the 40-case `regression-eval --enforce` gate, Ruff, mypy, and `uv build` on Python 3.11. It does not set `LLM_API_KEY`, run `--mode agent`, call live
providers, or depend on real network/API availability.

Real provider smoke testing is manual and documented in
`docs/SMOKE_TEST_AGENT_MODE.md`. It reads credentials only from environment
variables. Hardcoded keys and fallback credentials are forbidden.
`ModelProfile` files under `configs/model_profiles/` describe model
capabilities conservatively; they do not enable native tool calling.

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
Mock-agent and agent scans persist
`.signal-harness/latest_learning_observation.json` plus a demo copy under
`outputs/`; these files are observations, not applied configuration.

## Project independence

SignalHarness is maintained as its own project and the current package does not vendor or import OpenHarness runtime code. The repository retains an upstream remote/common Git ancestry from an earlier exploration stage, so the accurate provenance claim is that SignalHarness was substantially reworked around its own signal-intelligence use case rather than claiming no upstream history.

## Multi-step evidence reasoning

ContextEvidenceAgent first proposes read-only tool requests. Python validates
the allowlist, permissions, and tool budget, executes local SignalHarness
tools, and returns observations to a second evidence turn. Failed, blocked, or
budget-blocked tools do not crash the scan: uncertainty increases and
confidence is capped. Impact analysis then combines evidence, project context,
and lightweight multi-source clusters.

This remains a controlled runner loop, not provider-native function calling.
It also avoids implementing a complete handoff-as-tool protocol.
`AgentLoopLimits` bounds provider timeouts, schema retries, tool budgets, and
bounded repair limits. It separates per-Agent provider call timeout from the
whole Agent-team run timeout. Provider timeout falls back deterministically and
is visible in trace as `provider_timeout`; whole-run timeout is visible as
`agent_team_run_timeout`.

## Evaluation

SignalHarness has two eval layers. `regression-eval --enforce` is the product-level contract gate: the committed 40-case `resume-v1` corpus currently passes 40/40 exact decisions, 40/40 categories, 100% priority precision/recall, and 0% FPR/FNR in offline mock-agent mode. This is project-specific regression evidence, not a general LLM benchmark.

`model-eval` is the provider contract eval. It records schema valid rate, retry/fallback/timeout, tool validation/block/budget/runtime errors, repair counts, run-state isolation, latency, provider-reported tokens, and estimated cost when pricing metadata exists. For `--runs N` greater than one, state is isolated per run. Historical real-provider snapshots are dated evidence only. See `docs/EVALS.md`.

## Skipped routes and audit defaults

Supervisor routing controls actual downstream LLM execution. When a route
skips an event or stage, deterministic fallback can still populate the stored
assessment so every input has a complete audit record. That fallback is an
audit default, not evidence that the skipped downstream Agent ran.

## Operational and service layer

The CLI remains the simplest one-shot execution surface. `signal-harness serve` adds three interfaces over the same Workflow: synchronous REST, a Golden Demo backed by Server-Sent Events, and `/mcp` Streamable HTTP; `signal-harness mcp` provides stdio MCP. Every service run gets isolated output/state directories.

The original `POST /runs` stays synchronous. `POST /stream-runs` creates an in-process queued run; the first SSE subscriber starts the workflow, so `/stream-runs/{id}/events` delivers real TraceRecorder append/update events rather than a finished-run animation. Event IDs support reconnect replay, and disconnecting the browser does not cancel the task. The replay buffer is intentionally memory-only and is not described as a durable queue or distributed worker system.

The MCP surface is read-only and exposes project context, signal history, assessments, trace, and feedback. It does not create a second write/permission path. Docker runs the same service entry point and has a `/health` healthcheck. Scheduled execution remains external to the core process.

## Bounded repair boundary

Repair is intentionally bounded and enabled only inside the fixed pipeline.
`ImpactAnalystAgent` may suggest ContextEvidence repair, and
`ActionPlannerAgent` may suggest Impact repair. Python enforces repair round
limits, event caps, and the shared tool budget; budgets do not reset and repair
does not recurse. This is not provider-native function calling and not a full
handoff-as-tool system. LearningPolicyAgent remains review-only; real interactive scans defer its LLM reflection to explicit calibration/learning, while mock-agent can still exercise the full five-Agent path. It
cannot repair upstream Agents or auto-apply policy, watchlist, or skill
changes.

## Learning staging

Learning proposals now go through a local staging gate:

```bash
uv run signal-harness learning-stage
uv run signal-harness learning-review
uv run signal-harness learning-apply --proposal-id <id> --yes
```

The source of truth is `.signal-harness/learning_staging.json`; demo copies are
written to `outputs/latest_learning_staging.json` and
`outputs/latest_learning_risk_report.md`. Threshold, permission, tool
permission, project profile, watchlist deletion, external notification, and
GitHub issue changes are high risk. Missing or negative replay keeps a proposal
staged for human review rather than auto-applying it.
`calibrate --apply` uses the same gate. Without `--yes` it stages the proposal
and prints review/apply instructions; with `--yes`, only a low-risk proposal
with a passing replay gate can be applied. High-risk or replay-failed proposals
remain staged and cannot be applied non-interactively.

## Engineering choices

GitHub/RSS/Web bodies and ToolObservation content are treated as untrusted external data. Embedded prompt overrides, forced classifications, and tool commands cannot override the Agent role, Python guardrails, or final scoring. Deterministic semantics strips instruction-like sentences before keyword matching while preserving original evidence for audit.

Prompt prefixes keep static instructions and stable project context first;
events, tool observations, failures, and timestamps are later. Hashes make this
visible without provider-specific cache APIs. Source collection remains
concurrent and synchronous from the CLI perspective, with one observable
`SourceTask` per source and no background queue.

Avoiding orchestration frameworks, databases, queues, embeddings, and vector
stores is a deliberate MVP choice, not an omission hidden by the architecture
diagram. SignalHarness now keeps a focused dependency set for provider
adaptation, local CLI/reporting, trace/eval, permission checks, and read-only
signal tools.

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

The optional agent-mode adapter targets structured JSON responses through
`LLM_API_KEY`, `LLM_MODEL`, `LLM_MODEL_PROFILE`, and optional `LLM_BASE_URL`.
Evidence Agents receive collected primary-source context and can declare tool
requests, but broad live search is not enabled in the restricted SignalHarness
tool registry. Source clustering is rule-based rather than semantic. REST/SSE has no production auth/multi-tenancy or durable distributed job queue; live SSE replay state is lost on service restart. Proposals are deliberately review-only. The project does not claim provider-native function calling, fully autonomous self-evolution, horizontal production scale, or a fully conversational multi-Agent debate runtime.
