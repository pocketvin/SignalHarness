# Real Agent Mode Smoke Test

This manual smoke test verifies the optional real-provider path. It is not
required for offline `demo` or `mock-agent` CI.

Public CI does not require a real API key. It uses `mock-agent` / scripted evals
only and does not call live providers. Hardcoded keys and fallback credentials
are forbidden; real provider credentials must come from environment variables.

## Configure the provider

```bash
export LLM_API_KEY="..."
export LLM_MODEL="gpt-4o-mini"

# Optional for an OpenAI-compatible provider:
export LLM_BASE_URL="https://your-provider.example/v1"
```

`LLM_API_KEY` is required. `LLM_MODEL` defaults to `gpt-4o-mini`.
`LLM_BASE_URL` is optional.

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

The smoke test validates:

- real provider calls through the optional provider integration;
- structured schema validation and deterministic fallback;
- controlled tool-request validation and observations;
- prompt and Agent trace fields;
- deterministic scoring and permission guards.

Real network calls may fail because of credentials, endpoint compatibility,
rate limits, model behavior, or provider availability. Such failures do not
invalidate offline `demo` or scripted `mock-agent` tests.

SignalHarness currently uses a runner-controlled two-turn tool loop. It does
not use provider-native function calling and does not implement full
handoff-as-tool.
