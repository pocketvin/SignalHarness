# SignalHarness Run Modes

## `demo`

`signal-harness scan --mode demo` runs the deterministic fallback. It needs no
LLM key and is intended for CI, offline demonstrations, and baseline testing.
It is not true multi-Agent execution.

## `mock-agent`

`signal-harness scan --mode mock-agent` uses `MockProvider`, needs no real key,
and runs the complete routed five-Agent architecture. The mock returns
schema-validated JSON through the same runner used by real providers.
ContextEvidenceAgent normally emits two `llm_agent_call` records—tool planning
and final evidence—while stages not required by any route may be skipped.

## `agent`

`signal-harness scan --mode agent` is the optional real-provider path. It
requires `LLM_API_KEY` and uses the same SignalHarness schemas, runner
guardrails, trace fields, and deterministic fallback as `mock-agent`.

If the key is missing, the CLI reports:

```text
agent mode requires LLM_API_KEY. Use --mode demo or --mode mock-agent for offline execution.
```

`LLM_MODEL` selects the model and `LLM_BASE_URL` may select an
OpenAI-compatible endpoint. This mode is for manual smoke testing; public CI
uses offline `mock-agent` checks.
