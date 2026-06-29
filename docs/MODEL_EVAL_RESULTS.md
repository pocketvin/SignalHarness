# Model Eval Results

`signal-harness model-eval` is a local Harness comparison, not a broad model
benchmark. It runs the same fixture through the selected mode and summarizes
schema, fallback, timeout, tool, decision, repair, and latency behavior.

## Command

```bash
uv run signal-harness model-eval \
  --fixture examples/signal_harness/sample_events.json \
  --mode mock-agent \
  --runs 2
```

For `--runs N` greater than one, SignalHarness uses isolated state directories:

```text
.signal-harness/model-eval/run-001/
.signal-harness/model-eval/run-002/
```

The summary remains under `outputs/`:

- `outputs/model_eval_summary.json`
- `outputs/model_eval_summary.md`

## Summary fields

The summary includes:

- provider, model, and model profile;
- `run_state_mode` and `isolated_state`;
- schema valid rate, retry rate, fallback rate, and timeout count;
- tool plan valid rate, blocked tools, budget blocks, and tool errors;
- decision counts, action-required count, and alert count;
- average LLM latency;
- repair requested, executed, blocked, and fallback counts.

## Reading the result

Use model-eval to compare whether a provider behaves safely under the same
SignalHarness constraints:

- Does it return valid JSON schemas?
- Does it trigger fallback or timeout often?
- Does it request unsupported tools?
- Does bounded repair stay rare and explainable?
- Does decision distribution remain stable on the fixture?

Because the summary is local and deterministic around the Harness, no Langfuse,
Ragas, hosted dashboard, Redis, Postgres, or vector database is required.

## Boundary

Model eval does not prove general model intelligence. It proves that a provider
can participate in SignalHarness’ structured Agent contract, controlled
tool-use loop, deterministic fallback, and local observability.
