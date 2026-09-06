# SignalHarness Showcase

## Dependency release radar

A tracked repository publishes a checkpoint migration release. SignalHarness:

1. normalizes the GitHub payload;
2. asks SignalSupervisorAgent to classify and route it;
3. asks ContextEvidenceAgent to preserve and assess the primary URL;
4. asks ImpactAnalystAgent to judge semantic relevance and affected modules;
5. asks ActionPlannerAgent for a bounded manual review;
6. calculates the guarded score and records all calls in the trace.

## Tool-governance signal

An upstream issue proposes runtime tool allowlists. SignalHarness can save it as
a lower-priority architecture signal, while its own registry demonstrates the
same pattern by excluding shell, edit, browser, notebook, MCP, and task tools.

## Feedback calibration

After a user marks a checkpoint signal useful, SignalHarness records the
judgment and proposes adding “checkpoint” to suggested focus terms. The
proposal is reviewable JSON and does not alter the YAML policy until explicitly
approved. LearningPolicyAgent also proposes skill and watchlist changes, while a
deterministic replay evaluator compares old and proposed policy behavior.

## Regression gate

The `resume-v1` corpus contains 40 labelled product cases. The current offline mock-agent gate passes 40/40 decisions and categories with 100% priority precision/recall and zero priority false positives/negatives. This is product contract evidence, not a general model benchmark.

## External interface showcase

`signal-harness serve` exposes REST run/trace/signal/feedback endpoints and MCP Streamable HTTP. `signal-harness mcp` exposes the same five read-only MCP tools over stdio. Both reuse the core domain/runtime instead of introducing a second scoring or permission implementation.
