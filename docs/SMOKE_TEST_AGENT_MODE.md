# Real Agent Mode Smoke Test

This manual smoke test verifies the optional real-provider path. It is not
required for offline `demo` or `mock-agent` CI.

Public CI does not require a real API key. It runs SignalHarness-focused
offline checks only: `tests/signal_harness`, Ruff, mypy, and `uv build`.
It does not run `--mode agent` and does not call live providers. Hardcoded keys
and fallback credentials are forbidden; real provider credentials must come
from environment variables.

## Configure the provider

```bash
export LLM_API_KEY="..."
export LLM_PROVIDER="openai_compatible"
export LLM_MODEL="gpt-4o-mini"
export LLM_MODEL_PROFILE="openai_gpt4o_mini"

# Optional for an OpenAI-compatible provider:
export LLM_BASE_URL="https://your-provider.example/v1"
```

`LLM_API_KEY` is required. `LLM_PROVIDER` defaults to `openai_compatible`.
`LLM_MODEL` defaults to the model in the selected profile, and `LLM_BASE_URL`
is optional. Unsupported provider values fail clearly.

Available profile names include `openai_gpt4o_mini`, `kimi`, `qwen`, and
`deepseek`. Profiles describe model capabilities conservatively; they do not
turn on provider-native tool calling.

## Run the smoke test

```bash
uv run signal-harness scan \
  --fixture examples/signal_harness/sample_events.json \
  --mode agent

uv run signal-harness trace
```

Inspect `outputs/task_trace.json` and `outputs/trace_summary.md`.
If a provider returns invalid structured JSON, SignalHarness records the schema
error, retries the Agent once with a repair prompt, and then falls back to the
deterministic guardrail path if validation still fails.
If a provider call exceeds `AgentLoopLimits.max_agent_call_seconds`,
SignalHarness records a `provider_timeout` trace error and falls back.
If the whole Agent team exceeds `AgentLoopLimits.max_run_seconds`,
SignalHarness records `agent_team_run_timeout` and uses deterministic audit
fallback rather than hanging.

The smoke test validates:

- real provider calls through the optional provider integration;
- structured schema validation and deterministic fallback;
- LLM timeout handling through `AgentLoopLimits`;
- bounded repair trace behavior if repair is requested;
- controlled tool-request validation and observations;
- prompt and Agent trace fields;
- deterministic scoring and permission guards.

Real network calls may fail because of credentials, endpoint compatibility,
rate limits, model behavior, or provider availability. Such failures do not
invalidate offline `demo` or scripted `mock-agent` tests.

For a local provider matrix, copy `.env.example` to `.env`, fill provider keys
locally, and run:

```bash
bash scripts/model_eval_matrix.sh --providers openai,qwen,deepseek --runs 3
bash scripts/model_eval_matrix.sh --providers kimi --runs 1 --sleep 10
```

The script does not print API keys and writes only local ignored outputs.

SignalHarness currently uses a runner-controlled two-turn tool loop. It does
not use provider-native function calling and does not implement full
handoff-as-tool.
