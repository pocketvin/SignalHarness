# SignalHarness

SignalHarness is an LLM-native multi-agent signal intelligence harness.
It uses deterministic components as guardrails, fallback, permission control,
scoring constraints, and traceability layers.

Built on HKUDS/OpenHarness, SignalHarness turns GitHub, RSS, and web-change
events into project-specific evidence, impact judgments, action plans, and
reviewable learning proposals.

## Fixed five-Agent team

The real Agent path is implemented in `src/signal_harness/agent_team/`:

1. `SignalSupervisorAgent` classifies and routes the event batch.
2. `ContextEvidenceAgent` enriches context and verifies evidence.
3. `ImpactAnalystAgent` judges affected modules, semantic relevance, and risk.
4. `ActionPlannerAgent` proposes bounded actions and critic notes.
5. `LearningPolicyAgent` reflects over memory and creates approval-gated
   policy, skill, and watchlist proposals.

Memory is infrastructure, not an Agent. The four stores are `ProjectMemory`,
`SignalMemory`, `FeedbackMemory`, and `PolicyMemory`.

## Run modes

```bash
# Offline deterministic fallback for CI and demos
uv run signal-harness scan \
  --fixture examples/signal_harness/sample_events.json \
  --mode demo

# Offline scripted provider, but the real five-Agent architecture
uv run signal-harness scan \
  --fixture examples/signal_harness/sample_events.json \
  --mode mock-agent

# Real OpenHarness-backed provider
LLM_API_KEY=... uv run signal-harness scan \
  --fixture examples/signal_harness/sample_events.json \
  --mode agent
```

`demo` is not presented as true multi-Agent execution. `mock-agent` and
`agent` use the same routed five-Agent architecture. A single Agent may have
multiple turns: ContextEvidenceAgent first proposes tool requests and then
reads controlled observations before producing final evidence.

Optional real-provider environment variables:

- `LLM_MODEL` (default: `gpt-4o-mini`)
- `LLM_BASE_URL` for an OpenAI-compatible endpoint

## Guarded score

The LLM supplies semantic relevance and evidence confidence, but it cannot emit
the authoritative final score. Python calculates a deterministic base score,
validates every Agent schema, blends approved components with policy weights,
and applies the category multiplier:

```text
guarded_final_score =
  (
      deterministic_base * 0.70
    + semantic_relevance * 0.20
    + evidence_confidence * 0.10
  ) * policy_multiplier
```

`ImpactAnalystAgent` has no `final_score` field in its output schema. Extra
fields fail validation and trigger deterministic fallback.

## Feedback and learning

```bash
uv run signal-harness feedback \
  --signal-id demo-001 \
  --label useful \
  --note "checkpoint and memory signals are important"

uv run signal-harness calibrate --mode mock-agent
```

Calibration writes review-only artifacts under `.signal-harness/`:

- `policy_update_proposal.json`
- `skill_update_proposal.md`
- `watchlist_update_proposal.json`
- `replay_evaluation.json`

`.signal-harness/` remains the state source of truth. For demos, calibration
also copies the latest read-only snapshots to:

- `outputs/latest_policy_update_proposal.json`
- `outputs/latest_skill_update_proposal.md`
- `outputs/latest_watchlist_update_proposal.json`
- `outputs/latest_replay_evaluation.json`

These snapshots do not mean that any proposal was applied.

No proposal is applied automatically. `signal_policy.yaml`, skill files, and
`watchlist.yaml` change only through a separate explicit approval path.

## Trace and outputs

```bash
uv run signal-harness report
uv run signal-harness trace
```

Every scan writes:

```text
outputs/
├── signals.json
├── impact_scores.json
├── action_items.json
├── task_trace.json
├── radar_digest.md
└── run_summary.txt
```

Each LLM trace record includes Agent name, mode, model, prompt version, input
event IDs, output schema, schema validity, fallback status, duration, requested
and executed tools, blocked tools, permission checks, cache events, context
hashes, and errors. Deterministic stages remain visible alongside
`llm_agent_call` records.

## Controlled evidence tools and context

ContextEvidenceAgent uses a two-turn controlled loop:

1. The model returns `ToolRequest` objects.
2. Python validates the read-only allowlist and permission policy.
3. Existing OpenHarness `SignalToolExecutor` tools run.
4. `ToolObservation` objects are appended to the second Agent turn.
5. Failed or blocked tools increase uncertainty and cap confidence.

This is controlled orchestration by the SignalHarness runner, not
provider-native function calling and not a complete handoff-as-tool system.

Prompt context is layered as static instructions, stable project/policy
summary, semi-stable memory summary, dynamic task data, and volatile run
metadata. Trace records stable-prefix and dynamic-context hashes; no
provider-specific prompt-cache API is required.

## Lightweight engineering choices

The pre-LLM `NoiseFilter` downweights or routes obvious noise without deleting
raw signals. `SignalClusterer` groups related GitHub, RSS, and web-change events
using source, domain, token, and time overlap. Local JSON source caching and
per-run tool-observation caching require no service process. Concurrent source
tasks record duration, output count, cache hits, and independent failures.

OpenHarness is the only Agent Harness foundation. LangGraph, CrewAI, and
AutoGen are references or competitors, not runtime dependencies. Redis,
Postgres, Celery, vector databases, and embedding databases are intentionally
excluded from this MVP.

## OpenHarness integration

`src/signal_harness/providers/openharness_provider.py` is deliberately thin. It
reuses OpenHarness's `OpenAICompatibleClient`, `ApiMessageRequest`,
`ConversationMessage`, streaming events, retries, and provider error handling.
SignalHarness does not contain a second LLM runtime and does not rewrite the
OpenHarness query engine.

The deterministic layer retains normalization, deduplication, base scoring,
schema validation, permission enforcement, reporting, persistence, replay
evaluation, and fallback behavior.

When Supervisor routing skips an event or downstream stage, deterministic
fallback may still populate evidence, impact, or action-shaped fields so the
stored assessment remains complete for audit. Those values are audit defaults,
not downstream LLM Agent execution; the trace marks them as
`skipped_event_audit_fallback`.

## Project structure

```text
src/signal_harness/
├── agent_team/         # The fixed five LLM Agents
├── agent_integration/  # Prompts, schemas, mode, runner, LLM trace
├── providers/          # Thin OpenHarness and mock adapters
├── memory/             # Four memory stores and replay evaluation
├── agents/             # Legacy deterministic fallback specialists
├── runtime/            # Workflow, permission, tools, trace
├── signal/             # Schemas, normalization, noise, clustering, scoring
├── tools/              # OpenHarness-native domain tools
└── ui/                 # Terminal and trace views
```

## Verification

```bash
uv run --extra dev python -m pytest tests/signal_harness -q
uv run --extra dev python -m pytest -q
uv run --extra dev ruff check src/signal_harness tests/signal_harness
uv run --extra dev mypy src/signal_harness
uv build
```

CI evals use `MockProvider(strategy="scripted")` and the multi-source fixture.
They evaluate routing, evidence, tool controls, noise, caching, clustering,
guardrails, and proposal safety—not model intelligence. Real `agent` mode
remains a manual smoke test.

Repo-local reusable workflow notes live under `.agents/skills/`. They are for
project collaboration and Codex reading; the project does not assume that this
directory is automatically loaded by every Agent runtime. Existing
`.claude/skills/` assets remain available separately.

See:

- [Run modes](docs/RUN_MODES.md)
- [LLM Agent architecture](docs/LLM_AGENT_ARCHITECTURE.md)
- [Self-improvement loop](docs/SELF_IMPROVEMENT_LOOP.md)
- [Provider integration](docs/PROVIDER_INTEGRATION.md)
- [Interview guide](docs/INTERVIEW_GUIDE.md)
- [Real agent-mode smoke test](docs/SMOKE_TEST_AGENT_MODE.md)

## Attribution

SignalHarness includes modified components derived from
[HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness). The MIT license and
attribution are preserved in [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md).
