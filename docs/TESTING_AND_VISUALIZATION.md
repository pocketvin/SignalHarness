# Testing and Visualization

## Focused tests

```bash
uv run --extra dev python -m pytest tests/signal_harness -q
uv run --extra dev ruff check src/signal_harness tests/signal_harness
uv run --extra dev mypy src/signal_harness
```

Coverage includes:

- all three run modes and the missing-key error;
- the exact five-Agent mock path;
- valid structured output and invalid-JSON fallback;
- final-score ownership and deterministic guardrails;
- permission checks for LLM-requested high-risk actions;
- LLM trace fields and fallback visibility;
- four memory stores and LearningPolicyAgent naming;
- policy, skill, and watchlist proposals;
- replay comparison and no automatic configuration mutation;
- OpenHarness provider-client reuse and thin adapter boundaries.

## Compatibility regression

```bash
uv run --extra dev python -m pytest -q
uv build
```

## Demo

```bash
uv run signal-harness scan \
  --fixture examples/signal_harness/sample_events.json \
  --mode mock-agent
uv run signal-harness report
uv run signal-harness trace
```

The trace view labels deterministic stages and LLM Agent calls separately and
shows fallback and permission checks.
