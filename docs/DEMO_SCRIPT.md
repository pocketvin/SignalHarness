# SignalHarness Demo Script

This script is designed for a short interview or portfolio walkthrough. It
shows SignalHarness as an independent LLM-enhanced signal intelligence project,
not as a repackaged Agent framework.

## Three-minute demo

1. Open with the problem.

   SignalHarness monitors project-environment signals from GitHub, RSS, and web
   changes. It verifies evidence, estimates project impact, proposes safe
   actions, and creates review-only learning proposals.

2. Run the offline Agent demo.

   ```bash
   uv run signal-harness scan \
     --fixture examples/signal_harness/sample_events.json \
     --mode mock-agent
   ```

   Explain that `mock-agent` uses an offline scripted provider but still
   exercises the real five-Agent routed architecture.

3. Inspect the trace.

   ```bash
   uv run signal-harness trace
   ```

   Point out schema validation, retry/fallback fields, tool requests, executed
   tools, blocked tools, permission checks, budget blocks, and exit conditions.

4. Open the local dashboard and alerts.

   ```bash
   uv run signal-harness dashboard
   uv run signal-harness digest --period daily
   ```

   Show `outputs/dashboard.html`, `outputs/alerts.md`, and
   `outputs/daily_digest.md`. Emphasize that these are local files; the LLM
   does not send notifications.

5. Show learning as review-only.

   ```bash
   uv run signal-harness calibrate --mode mock-agent
   ```

   Open the latest proposal snapshots under `outputs/`. Explain that
   `.signal-harness/` is the state source of truth and proposals are never
   applied automatically.

## Five-minute interview narrative

Use this structure:

- Situation: Agent projects often look impressive but hide fragile execution,
  unclear permissions, and weak traceability.
- Task: Build a focused signal-intelligence harness that demonstrates
  real Agent engineering without outsourcing the interesting parts to a large
  orchestration framework.
- Action: Implemented a five-Agent routed workflow, a controlled two-step
  tool-use loop, schema-first outputs, deterministic scoring/fallback, local
  trace/eval, and a lightweight operational layer.
- Result: The project runs fully offline for demos and CI, can smoke-test real
  providers manually, and produces auditable JSON/Markdown/HTML outputs.

## Mode explanation

- `demo`: deterministic fallback only. Useful for CI and offline baseline
  behavior. Not true LLM Agent execution.
- `mock-agent`: offline scripted provider. Exercises the real
  SignalSupervisorAgent, ContextEvidenceAgent, ImpactAnalystAgent,
  ActionPlannerAgent, and LearningPolicyAgent path without API keys.
- `agent`: optional real-provider path. Uses environment variables and the same
  schema validation, permission guard, trace, retry, and fallback boundaries.

## What to highlight

- LLM Agents can request tools, but Python owns tool permission, budget,
  execution, and observations.
- `ImpactAnalystAgent` cannot emit the final score. Python owns final scoring.
- `LearningPolicyAgent` creates review-only proposals; it does not mutate
  policy, watchlists, or skills.
- Trace files explain what ran, what was skipped, which tools were blocked, and
  when deterministic audit fallback filled a skipped event.
- The operational layer is intentionally lightweight: external schedulers,
  static HTML dashboard, local alerts, Markdown digests.

## What not to claim

- Do not claim provider-native function calling.
- Do not claim fully autonomous self-evolution.
- Do not claim a conversational multi-Agent debate runtime.
- Do not claim LangGraph/CrewAI/AutoGen/LangChain/LlamaIndex/Haystack/DSPy/
  Langfuse/Ragas integration.
