# Bounded Repair Pass

SignalHarness repair is a small Python-owned correction pass inside the fixed
five-Agent pipeline. It is not ReAct, not provider-native function calling, and
not a full handoff-as-tool implementation.

## Fixed pipeline

```text
SignalSupervisorAgent
  → ContextEvidenceAgent plan
  → Python tool execution
  → ContextEvidenceAgent final
  → ImpactAnalystAgent
  → optional ContextEvidence repair
  → optional Impact rerun
  → ActionPlannerAgent
  → optional Impact repair
  → optional Action rerun
  → LearningPolicyAgent
```

Only two repair directions are allowed:

- Impact→ContextEvidence: Impact can suggest evidence repair when high-risk
  impact depends on weak evidence.
- Action→Impact: Action can suggest impact repair when requested actions or
  approval requirements do not match the impact assessment.

Learning never repairs upstream Agents. Supervisor is not repaired. There is no
recursive repair loop.

## Limits

`AgentLoopLimits` owns the repair envelope:

- `max_repair_rounds_per_run`
- `max_repair_events_per_run`
- shared `max_total_tool_requests_per_run`
- shared `max_tool_requests_per_event`
- shared `max_tool_output_chars`

Tool budget is not reset during repair. If the first evidence pass used the
budget, repair evidence requests are blocked and recorded in trace rather than
executed.

## Triggers

Impact→Evidence can be triggered by:

- explicit `ImpactOutput.repair_requests` with
  `target_agent="context_evidence"`;
- deterministic guardrail: high/critical risk, evidence confidence below
  `0.45`, and semantic relevance at least `70`.

Action→Impact can be triggered by:

- explicit `ActionOutput.repair_requests` with `target_agent="impact"`;
- deterministic guardrail: requested actions with low semantic relevance, or
  approval required while risk is low.

## Trace

Repair uses ordinary `TraceStep` entries:

- `repair_requested`
- `repair_context_evidence`
- `repair_impact`
- `repair_action`
- `repair_blocked`

`signal-harness trace` summarizes requested, executed, blocked, fallback, and
event IDs. `signal-harness dashboard` shows an Agent repair pass section; when
no repair runs it says “No repair pass was triggered.”

## Interview phrasing

Say: “Agents can suggest repair, but Python decides. The repair pass is bounded,
non-recursive, and uses the same tool budget.”

Do not say: “SignalHarness has autonomous handoff tools,” “the provider calls
tools natively,” or “Learning can rewrite the pipeline.”
