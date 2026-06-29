# LLM Agent Architecture

SignalHarness has one fixed LLM-enhanced routed Agent team:

1. `SignalSupervisorAgent` receives a `SignalEvent` batch, classifies, routes,
   and decides which events merit analysis. It cannot perform deep evidence
   work, generate actions, update policy, or override final scores.
2. `ContextEvidenceAgent` first returns `EvidenceToolPlan` with `ToolRequest`
   objects. The runner validates a read-only allowlist, permissions, and
   lightweight tool budgets, executes local SignalHarness tools, then supplies
   `ToolObservation` objects to a second turn that returns final evidence.
3. `ImpactAnalystAgent` reads the project profile and evidence and returns
   affected modules, semantic relevance, risk, and impact reasoning. Its schema
   intentionally has no `final_score`.
4. `ActionPlannerAgent` proposes non-mutating actions, performs a light critic
   pass, and flags approval requirements. Requested high-risk operations are
   always rechecked by `SignalPermissionGuard`.
5. `LearningPolicyAgent` reads the four memory stores and produces review-only
   policy, skill, and watchlist proposals.

The architecture has five Agent roles, not a fixed number of model turns.
ContextEvidenceAgent normally uses two turns, and routed stages may be skipped
when `required_agents` does not include them. Noise receives no deep-analysis
calls; low-priority expert opinion can stop after evidence and impact.

If routing skips an event or a downstream stage, deterministic fallback may
fill the remaining assessment fields solely to preserve a complete audit
record. This does not mean ContextEvidenceAgent, ImpactAnalystAgent, or
ActionPlannerAgent executed for that skipped event. The trace emits
`skipped_event_audit_fallback` with this distinction.

## Prompt context layers

`PromptContextBuilder` orders:

1. static Agent instructions, boundaries, schema, and guardrail rules;
2. stable project, policy, tool allowlist, reliability, and skill summaries;
3. compressed semi-stable memory summaries;
4. dynamic events, routes, clusters, evidence, impact, and observations;
5. volatile run ID, provider/model, failures, retries, and timestamps.

JSON is serialized with sorted keys. Trace stores `prompt_prefix_hash`,
`static_context_hash`, `dynamic_context_hash`, packet version, and cache
strategy. Volatile metadata never enters the static prefix.

## Multi-source and deterministic boundaries

`NoiseFilter` supplies conservative route hints. `SignalClusterer` groups
related sources with lightweight token/time rules; same source or same domain
can lower the token threshold, but neither is sufficient by itself.
ImpactAnalystAgent receives clusters and can report related events,
cross-source confidence, and conflicting evidence. Python still validates
schemas, owns tools and permissions, calculates final scores, and supplies
fallback.

The tool loop is deliberately runner-controlled. It is not provider-native
function calling, and SignalHarness does not implement complete
handoff-as-tool orchestration.

Real agent mode still asks the provider for structured JSON. If JSON parsing,
schema validation, or an empty response fails, the runner retries once with a
short schema-correction instruction. A second failure triggers deterministic
fallback.

`AgentLoopLimits` bounds `max_schema_retries`, provider-call timeout, whole
Agent-team run timeout, total tool requests, per-event tool requests, tool
output size, and bounded repair round/event limits. Provider timeouts are
recorded as `provider_timeout` in the LLM trace. Whole-run timeouts are
recorded as `agent_team_run_timeout`. Both use deterministic fallback.

The default real-provider path is `OpenAICompatibleProvider`, configured with
`LLM_PROVIDER=openai_compatible`, `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`,
and `LLM_MODEL_PROFILE`. `OpenHarnessProvider` remains an optional
compatibility integration. `ModelProfile` documents capabilities but does not
enable native tool calling.

`model-eval` computes local metrics from trace and assessments so different
models can be compared under the same SignalHarness constraints.

Bounded repair is not an open ReAct loop. The implementation supports only
Impact→ContextEvidence and Action→Impact repair, enforces
`max_repair_rounds_per_run` plus `max_repair_events_per_run`, reuses the same
tool budget, and never lets LearningPolicyAgent repair upstream Agents or
mutate configuration. Agents suggest repair; Python decides whether to run it.

The older classes in `src/signal_harness/agents/` are deterministic fallback
specialists. They are not described as the true multi-Agent implementation.
