# SignalHarness

SignalHarness is a standalone LLM-enhanced routed multi-agent signal
intelligence harness.

It monitors project-environment signals from GitHub, RSS, and web changes,
verifies evidence, analyzes project impact, proposes safe actions, and produces
review-only learning proposals. Python owns tool permission, budget, schema
validation, fallback, trace, outputs, and final scoring.

SignalHarness is not provider-native function calling, not a fully autonomous
self-evolving system, not a fully conversational multi-agent debate runtime,
and not a LangGraph/CrewAI/AutoGen wrapper. The LLM never directly executes
tools or applies configuration changes.

The project originated as OpenHarness downstream work and keeps MIT attribution
for reused or adapted ideas/code. OpenHarness is no longer the public identity
of this repository; optional OpenHarness-compatible provider integration lives
behind the `agent` mode path.

## Core architecture

The real Agent path is implemented in `src/signal_harness/agent_team/`:

1. `SignalSupervisorAgent` classifies and routes the event batch.
2. `ContextEvidenceAgent` enriches context and verifies evidence.
3. `ImpactAnalystAgent` judges affected modules, semantic relevance, and risk.
4. `ActionPlannerAgent` proposes bounded actions and critic notes.
5. `LearningPolicyAgent` reflects over memory and creates approval-gated
   policy, skill, and watchlist proposals.

Memory is infrastructure, not an Agent. The four stores are `ProjectMemory`,
`SignalMemory`, `FeedbackMemory`, and `PolicyMemory`.

## Safety boundaries

- LLM Agents return structured JSON; Python validates every schema.
- LLM Agents request tools; Python owns allowlists, permission checks, budgets,
  execution, and observations.
- `ImpactAnalystAgent` cannot emit the authoritative `final_score`.
- `LearningPolicyAgent` only creates review-only proposals.
- Repair is bounded by Python-owned run limits. Agents may suggest only
  Impact→Evidence or Action→Impact repair; there is no recursive handoff loop.
- `mock-agent` runs offline with mock-safe tool outputs and no API key.

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

# Real provider path, defaults to SignalHarness OpenAI-compatible HTTP adapter
LLM_API_KEY=... uv run signal-harness scan \
  --fixture examples/signal_harness/sample_events.json \
  --mode agent
```

`demo` is not presented as true multi-Agent execution. `mock-agent` and
`agent` use the same routed five-Agent architecture. A single Agent may have
multiple turns: ContextEvidenceAgent first proposes tool requests and then
reads controlled observations before producing final evidence.

Optional real-provider environment variables:

- `LLM_PROVIDER` (default: `openai_compatible`; use `openharness` only for the
  optional compatibility adapter)
- `LLM_API_KEY`
- `LLM_MODEL` (default: `gpt-4o-mini`)
- `LLM_BASE_URL` for an OpenAI-compatible endpoint
- `LLM_MODEL_PROFILE` (`openai_gpt4o_mini`, `kimi`, `qwen`, `deepseek`, or a
  YAML path under `configs/model_profiles/`)

`ModelProfile` documents conservative model capabilities such as JSON mode,
system prompt support, token limits, and strategy names. It does not enable
provider-native tool calling: the default `tool_strategy` remains
`controlled_tool_request`, and `supports_native_tool_calling` is false.

## Public-safe CI and secrets policy

Public CI is SignalHarness-focused and offline: it runs
`python -m pytest tests/signal_harness -q`, Ruff, mypy, and `uv build` on
Python 3.11. It does not set `LLM_API_KEY`, does not run `--mode agent`, and
must not call live providers. Scripted `mock-agent` behavior is covered by the
SignalHarness test suite and local acceptance commands, while real provider
checks remain manual smoke tests documented in `docs/SMOKE_TEST_AGENT_MODE.md`.

Hardcoded API keys and secret-looking fallback credentials are forbidden. Use
environment variables such as `LLM_API_KEY` or `ANTHROPIC_API_KEY` for manual
smoke tests, and keep `.env`, runtime outputs, caches, and build artifacts out
of git.

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

`mock-agent` and `agent` scans also save the latest learning observation to
`.signal-harness/latest_learning_observation.json` and
`outputs/latest_learning_observation.json`. This records what
LearningPolicyAgent proposed for review; it does not apply policy, edit
watchlists, or modify skills.

No proposal is applied automatically. `signal_policy.yaml`, skill files, and
`watchlist.yaml` change only through a separate explicit approval path.
`calibrate --apply` also goes through this staging gate: without `--yes` it
stages and prints review/apply instructions; with `--yes`, only a low-risk
proposal with a passing replay gate can be applied through the same
`learning-apply` safeguards.
The staged approval path is:

```bash
uv run signal-harness learning-stage
uv run signal-harness learning-review
uv run signal-harness learning-apply --proposal-id <id> --yes
```

`learning-stage` writes `.signal-harness/learning_staging.json` and a demo copy
at `outputs/latest_learning_staging.json`, plus
`outputs/latest_learning_risk_report.md`. The deterministic risk classifier
marks threshold, permission, watchlist deletion, external notification, GitHub
issue, project profile, and tool-permission changes as high risk. A replay gate
must pass before a low-risk proposal can be applied; missing replay keeps the
proposal staged for review.

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
├── alerts.json
├── alerts.md
├── dashboard.html
├── radar_digest.md
└── run_summary.txt
```

Each LLM trace record includes Agent name, mode, model, prompt version, input
event IDs, output schema, schema validity, retry count, schema error,
fallback status, duration, requested and executed tools, budget-blocked tools,
blocked tools, permission checks, cache events, context hashes, and errors.
Deterministic stages remain visible alongside
`llm_agent_call` records.
`AgentLoopLimits` bounds schema retries, per-Agent provider call timeout, whole
Agent-team run timeout, tool request budget, tool output size, and bounded
repair pass limits. Provider timeouts are recorded in trace as
`provider_timeout` schema/error details. Whole-run timeouts are recorded as
`agent_team_run_timeout`. Both paths trigger deterministic fallback instead of
hanging.

## Operational layer

SignalHarness stays a one-shot scan engine. Scheduling is handled by external
platforms such as GitHub Actions, cron, or launchd; see
[Scheduled runs](docs/SCHEDULED_RUNS.md).

```bash
uv run signal-harness dashboard
uv run signal-harness digest --period daily
uv run signal-harness digest --period weekly
uv run signal-harness model-eval \
  --fixture examples/signal_harness/sample_events.json \
  --mode mock-agent
```

The deterministic `AlertPolicy` writes:

- `outputs/alerts.json`
- `outputs/alerts.md`
- `.signal-harness/alert_state.json`

Alert dispatch defaults to local files only. The LLM cannot directly send
notifications, and no Slack/Discord/Telegram/Feishu dependency is included.

`model-eval` writes `outputs/model_eval_summary.json` and
`outputs/model_eval_summary.md`. It compares models under the same Harness
metrics: schema valid rate, retry/fallback rate, timeout count, tool budget
blocks, blocked tools, tool errors, decision counts, repair counts, run-state
isolation mode, and average LLM latency. When `--runs N` is greater than one,
each run gets an isolated state directory under
`.signal-harness/model-eval/run-001`, `run-002`, and so on; the summary remains
under `outputs/`.

## Controlled evidence tools and context

ContextEvidenceAgent uses a two-turn controlled loop:

1. The model returns `ToolRequest` objects.
2. Python validates the read-only allowlist and permission policy.
3. Python applies lightweight budgets: at most 20 tool requests per run, 3 per
   event, and 1000 output characters per observation by default.
4. SignalHarness local `SignalToolExecutor` tools run.
5. `ToolObservation` objects are appended to the second Agent turn.
6. Failed, blocked, or budget-blocked tools increase uncertainty and cap
   confidence.

This is controlled orchestration by the SignalHarness runner, not
provider-native function calling and not a complete handoff-as-tool system.

## Bounded repair pass

SignalHarness keeps the main pipeline fixed:

```text
Supervisor → Evidence plan/tools/final → Impact
  → optional Evidence repair → optional Impact rerun
  → Action → optional Impact repair → optional Action rerun
  → Learning
```

Only two repair directions exist:

- `ImpactAnalystAgent` may suggest `target_agent=context_evidence` when evidence
  is too weak for a high-risk impact claim.
- `ActionPlannerAgent` may suggest `target_agent=impact` when actions and
  impact risk appear inconsistent.

Python decides whether repair runs. `max_repair_rounds_per_run`,
`max_repair_events_per_run`, and the shared tool budget are enforced by the
runner; budgets do not reset for repair. Repair events are merged back into the
existing evidence/impact/action outputs. Learning cannot repair upstream Agents,
and repair never becomes provider-native function calling.

Prompt context is layered as static instructions, stable project/policy
summary, semi-stable memory summary, dynamic task data, and volatile run
metadata. Trace records stable-prefix and dynamic-context hashes; no
provider-specific prompt-cache API is required.

## Lightweight engineering choices

The pre-LLM `NoiseFilter` downweights or routes obvious noise without deleting
raw signals. `SignalClusterer` groups related GitHub, RSS, and web-change events
only when token overlap and time proximity agree; same source or same domain is
not enough by itself. Local JSON source caching and per-run tool-observation
caching require no service process. Concurrent source tasks record duration,
output count, cache hits, and independent failures.

LangGraph, CrewAI, AutoGen, LangChain, LlamaIndex, Haystack, DSPy, Langfuse,
Ragas, Redis, Postgres, Celery, vector databases, and embedding databases are
intentionally excluded from this project.

## Provider integration

`src/signal_harness/providers/openai_compatible_provider.py` is the default
real-provider path for `--mode agent`. It uses the existing `httpx` dependency
and standard `/v1/chat/completions`-style assistant text. That text is still
parsed by the existing schema retry/fallback path.

`src/signal_harness/providers/openharness_provider.py` is deliberately optional.
Demo and mock-agent modes do not import or require the upstream OpenHarness
runtime. Set `LLM_PROVIDER=openharness` only when intentionally using that
compatibility path.

The deterministic layer retains normalization, deduplication, base scoring,
schema validation, permission enforcement, reporting, persistence, replay
evaluation, and fallback behavior.

When Supervisor routing skips an event or downstream stage, deterministic
fallback may still populate evidence, impact, or action-shaped fields so the
stored assessment remains complete for audit. Those values are audit defaults,
not downstream LLM Agent execution; the trace marks them as
`skipped_event_audit_fallback`.

## Dashboard explainability

`signal-harness dashboard` writes a static local HTML file with high-priority
signals, alerts, source health, top modules, Agent trace/tool controls, Agent
repair pass status, guarded score breakdowns, model/profile/limit metadata, and
learning staging status. If no repair pass was triggered, the dashboard says so
explicitly.

## Project structure

```text
src/signal_harness/
├── agent_team/         # The fixed five LLM Agents
├── agent_integration/  # Prompts, schemas, mode, runner, LLM trace
├── providers/          # Mock adapter and optional provider integration
├── memory/             # Four memory stores and replay evaluation
├── agents/             # Legacy deterministic fallback specialists
├── runtime/            # Workflow, permission, tools, trace
├── signal/             # Schemas, normalization, noise, clustering, scoring
├── tools/              # SignalHarness domain tools
└── ui/                 # Terminal and trace views
```

## Verification

```bash
uv run --extra dev python -m pytest tests/signal_harness -q
uv run --extra dev ruff check src/signal_harness tests/signal_harness
uv run --extra dev mypy src/signal_harness
uv run signal-harness scan --fixture examples/signal_harness/sample_events.json --mode demo
uv run signal-harness scan --fixture examples/signal_harness/sample_events.json --mode mock-agent
uv run signal-harness trace
uv run signal-harness dashboard
uv run signal-harness digest --period daily
uv run signal-harness digest --period weekly
uv run signal-harness model-eval --fixture examples/signal_harness/sample_events.json --mode mock-agent
uv run signal-harness calibrate --mode mock-agent
uv build
```

Scripted eval tests use `MockProvider(strategy="scripted")` and the
multi-source fixture. They evaluate routing, evidence, tool controls, noise,
caching, clustering, guardrails, and proposal safety—not model intelligence.
Real `agent` mode remains a manual smoke test and is not part of public CI.

See:

- [Run modes](docs/RUN_MODES.md)
- [Demo script](docs/DEMO_SCRIPT.md)
- [LLM Agent architecture](docs/LLM_AGENT_ARCHITECTURE.md)
- [Self-improvement loop](docs/SELF_IMPROVEMENT_LOOP.md)
- [Bounded repair pass](docs/REPAIR_PASS.md)
- [Model eval results](docs/MODEL_EVAL_RESULTS.md)
- [Provider integration](docs/PROVIDER_INTEGRATION.md)
- [Interview guide](docs/INTERVIEW_GUIDE.md)
- [Real agent-mode smoke test](docs/SMOKE_TEST_AGENT_MODE.md)
- [Real source smoke test](docs/REAL_SOURCE_SMOKE.md)
- [Upstream attribution](docs/UPSTREAM_ATTRIBUTION.md)
- [Example fixtures](examples/signal_harness/README.md)

## Attribution

SignalHarness includes modified components derived from
[HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness). The MIT license and
attribution are preserved in [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md).
