# LLM Agent Architecture

SignalHarness has one fixed LLM-native team:

1. `SignalSupervisorAgent` receives a `SignalEvent` batch, classifies, routes,
   and decides which events merit analysis. It cannot perform deep evidence
   work, generate actions, update policy, or override final scores.
2. `ContextEvidenceAgent` first returns `EvidenceToolPlan` with `ToolRequest`
   objects. The runner validates a read-only allowlist and permissions, executes
   existing OpenHarness tools, then supplies `ToolObservation` objects to a
   second turn that returns final evidence.
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
related sources with lightweight source/domain/token/time rules.
ImpactAnalystAgent receives clusters and can report related events,
cross-source confidence, and conflicting evidence. Python still validates
schemas, owns tools and permissions, calculates final scores, and supplies
fallback.

The tool loop is deliberately runner-controlled. It is not provider-native
function calling, and SignalHarness does not implement complete
handoff-as-tool orchestration.

The older classes in `src/signal_harness/agents/` are deterministic fallback
specialists. They are not described as the true multi-Agent implementation.
