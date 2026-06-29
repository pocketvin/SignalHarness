# Provider Integration

SignalHarness core does not require a live provider. The supported default
paths are:

- `demo`: deterministic fallback, no LLM provider;
- `mock-agent`: scripted offline provider, no API key;
- `agent`: real-provider integration through the SignalHarness
  OpenAI-compatible adapter by default.

`MockProvider` implements the same tiny `AgentProvider` protocol used by the
runner, so offline evals exercise the real five-Agent routed path without
network access.

`OpenAICompatibleProvider` is the default real-provider path. It uses `httpx`
against a `/v1/chat/completions`-style endpoint and returns assistant text to
the existing Pydantic schema retry/fallback pipeline. Configure it with:

```bash
export LLM_PROVIDER=openai_compatible
export LLM_API_KEY="..."
export LLM_BASE_URL="https://api.openai.com"
export LLM_MODEL_PROFILE=openai_gpt4o_mini
```

Profiles live under `configs/model_profiles/` and include `openai_gpt4o_mini`,
`kimi`, `qwen`, and `deepseek`. A `ModelProfile` records conservative
capabilities such as JSON mode and token limits, but it does not enable native
tool calling. The default strategy remains:

```yaml
schema_strategy: prompt_json_retry
tool_strategy: controlled_tool_request
supports_native_tool_calling: false
```

`OpenHarnessProvider` is kept as an optional compatibility adapter. It is not
imported by demo or mock-agent mode. Set `LLM_PROVIDER=openharness` only when
intentionally using that optional upstream runtime.

The real-provider path still asks for structured JSON. SignalHarness does not
use provider-native function calling: the model returns plans and outputs,
while Python validates schemas, executes read-only tools, applies budgets,
records trace, and computes final scores.

`AgentLoopLimits` controls schema retry count, provider-call timeout, tool
budget, tool output size, and future repair-pass limits. A provider timeout is
recorded in trace as `provider_timeout` and uses deterministic fallback.

`signal-harness model-eval` can compare models through the same local metrics
without adding an external eval platform:

```bash
uv run signal-harness model-eval \
  --fixture examples/signal_harness/sample_events.json \
  --mode mock-agent
```

No `src/signal_harness/llm/` runtime exists. The deterministic core owns
normalization, deduplication, permissions, scoring constraints, persistence,
replay, reporting, and fallback.
