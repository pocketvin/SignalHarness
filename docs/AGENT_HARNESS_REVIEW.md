# Agent Harness Review

SignalHarness is a focused LLM-enhanced signal intelligence harness. It scans
project-environment signals, routes them through five structured Agents, lets
Python validate every contract, and writes local outputs and trace records that
can be explained in an interview.

## Fixed workflow instead of open ReAct

SignalHarness uses a fixed routed workflow rather than an open-ended ReAct
loop. The important design choice is bounded control:

1. `SignalSupervisorAgent` routes the batch.
2. `ContextEvidenceAgent` plans read-only tools, receives Python-executed tool
   observations, and writes evidence.
3. `ImpactAnalystAgent` estimates affected modules, semantic relevance, and
   risk, but cannot emit the final score.
4. `ActionPlannerAgent` proposes review actions and approval notes.
5. `LearningPolicyAgent` proposes review-only policy, skill, and watchlist
   changes.

This keeps tool use and repair understandable. The model can request tools or
suggest bounded repair, but Python owns validation, allowlists, budgets,
fallback, final scoring, replay gates, and persistence.

## Memory design

The current memory layer is domain memory:

- `ProjectMemory`: project profile and stable configuration.
- `SignalMemory`: seen signals and prior assessments.
- `FeedbackMemory`: human feedback records.
- `PolicyMemory`: current signal policy and staged proposals.

SignalHarness is not a chat assistant. It uses domain memory for project
configuration, seen signals, feedback, and policy proposals. Current run
context is passed through the workflow and trace. It does not need
conversation-style session memory at this stage.

Because the workflow is batch-oriented, complex context compression is not yet
necessary. Prompt context is already layered into stable instructions, stable
project/policy context, semi-stable memory summary, dynamic event data, and
volatile run metadata.

## Prompt cache readiness

Prompts are organized with stable prefixes first and dynamic content later.
Trace records include prompt prefix, static-context, and dynamic-context hash
metadata. This makes provider prompt-cache adoption possible later without
changing the Agent architecture.

SignalHarness intentionally does not implement semantic cache yet. The current
fixtures and use case benefit more from deterministic traceability and source
freshness than from embedding-based reuse.

## Tool boundary and guardrails

ContextEvidenceAgent uses a controlled two-turn tool loop:

1. The Agent returns an `EvidenceToolPlan`.
2. Python validates schema, tool allowlist, permission policy, and budgets.
3. Python executes allowed read-only tools.
4. The Agent receives `ToolObservation` objects and produces final evidence.

This is not native provider tool calling. It is also not a full handoff-as-tool
system. Tool validation errors, blocked tools, budget blocks, and runtime
errors are visible in trace and model-eval summaries.

## Evals and tracing

`signal-harness model-eval` compares providers with local metrics:

- schema valid rate;
- retry/fallback rate;
- timeout count;
- latency;
- repair requested/executed/blocked/fallback counts;
- tool validation/blocked/budget/runtime/total errors.

Tracing is local and file-based. It records Agent names, schemas, prompt
versions, retries, fallback, tool requests/execution/errors, permission checks,
repair summary steps, and skipped-event audit fallback semantics.

## Retry and bounded repair

Each structured Agent call gets one schema retry by default. A second failure
falls back deterministically.

Bounded repair is deliberately narrow:

- Impact may request ContextEvidence repair.
- Action may request Impact repair.
- Python enforces run-level and event-level repair caps.
- Tool budgets do not reset for repair.
- LearningPolicyAgent cannot auto-repair upstream Agents.

This preserves explainability while still showing a realistic repair pass.

## Learning guardrail

LearningPolicyAgent never applies high-risk configuration automatically.
Learning proposals are staged, risk classified, replay checked, and require
explicit `learning-apply --yes`. High-risk or replay-failed proposals remain
staged for human review.

## Independence

SignalHarness is an independent project inspired by general agent harness
design patterns. It does not vendor or depend on OpenHarness code.

## Next steps

- Add richer fixtures for long-context and conflicting-evidence model eval.
- Keep real provider eval manual and public CI offline.
- Improve provider compatibility profiles only when a real eval exposes a
  small, well-scoped issue.
- Avoid adding a heavy orchestration framework until the workflow genuinely
  needs dynamic graph state.
