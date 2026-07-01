# SignalHarness Demo Script

This script is designed for a short interview or portfolio walkthrough. It
shows SignalHarness as an independent LLM-enhanced signal intelligence project,
not as a repackaged Agent framework.

## Three-minute demo

### Path A: offline deterministic / mock fixture demo

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

This path uses `examples/signal_harness/sample_events.json`. It is stable,
requires no API key, and is appropriate for CI or an interview environment with
unreliable network access.

### Path B: live OpenAI showcase

Use this path when you want fresh watchlist data instead of the sample fixture.
Do not pass `--fixture`; that is what makes SignalHarness read
`configs/watchlist.yaml`.

```bash
set -a
source .env
set +a

export LLM_PROVIDER="openai_compatible"
export LLM_API_KEY="${OPENAI_API_KEY:-${OPENAI_KEY:-${OPENAI:-}}}"
export LLM_BASE_URL="${OPENAI_BASE_URL:-https://api.openai.com}"
export LLM_MODEL="${OPENAI_MODEL:-gpt-4o-mini}"
export LLM_MODEL_PROFILE="openai_gpt4o_mini"

SINCE="$(python - <<'PY'
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc) - timedelta(days=14)).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
PY
)"

uv run signal-harness scan \
  --mode agent \
  --since "$SINCE" \
  --max-events 20 \
  --max-events-per-source 8 \
  --output-dir outputs/openai-live-showcase \
  --state-dir .signal-harness/openai-live-showcase

uv run signal-harness dashboard --output-dir outputs/openai-live-showcase
uv run signal-harness trace --output-dir outputs/openai-live-showcase
```

The live default watchlist is `configs/watchlist.yaml`. The fixture-backed demo
watchlist lives in `configs/watchlist_demo.yaml`, and the raw fixture remains at
`examples/signal_harness/sample_events.json`.

For live showcases, prefer a recent 7–14 day window plus
`--max-events 20 --max-events-per-source 8`. Provider calls may still trigger
fallback because of context limits, rate limits, schema retries, or timeouts;
the dashboard explicitly reports fallback health and labels deterministic
fallback audit output.

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
- The live dashboard has an executive summary, source health, grouped
  dependency updates, LLM fallback health, and review-only learning language so
  the demo is explainable even when a real provider partially fails.

## What not to claim

- Do not claim provider-native function calling.
- Do not claim fully autonomous self-evolution.
- Do not claim a conversational multi-Agent debate runtime.
- Do not claim LangGraph/CrewAI/AutoGen/LangChain/LlamaIndex/Haystack/DSPy/
  Langfuse/Ragas integration.
