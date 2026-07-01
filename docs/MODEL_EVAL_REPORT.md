# SignalHarness Model Eval Report

Last updated: 2026-07-01 20:08 CST

This report records a current local fixture result. It is not a permanent model
ranking and should be re-run when fixtures, prompts, provider models, or API
settings change.

## Setup

- Fixture: `examples/signal_harness/sample_events.json`
- Mode: `agent`
- Matrix commands:
  - `bash scripts/model_eval_matrix.sh --providers openai,qwen,deepseek --runs 3`
  - `bash scripts/model_eval_matrix.sh --providers kimi --runs 1 --sleep 10`
  - Kimi `runs=3` was not run because the `runs=1` result was not stable.
- Provider path: SignalHarness OpenAI-compatible HTTP adapter
- Tool loop: runner-controlled `EvidenceToolPlan` → Python validation/execution
  → `ToolObservation` → final evidence
- CI impact: none. Public CI still uses offline mock-agent/scripted eval only.

`complete_stable` now requires all of the following on the current fixture:
`schema_valid_rate >= 0.99`, `fallback_rate == 0`, `retry_rate == 0`,
`timeout_count == 0`, and `total_tool_error_count == 0`.

## Current local results

| Provider | Model | Runs | Result | Schema valid | Fallback | Retry | Timeout | Tool total | Avg latency ms | Repair requested | Repair executed |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| OpenAI | `gpt-4o-mini` | 3 | `complete_stable` | 1.0000 | 0.0000 | 0.0000 | 0 | 0 | 7388.33 | 0 | 0 |
| Qwen | `qwen-plus` | 3 | `complete_unstable` | 0.8889 | 0.1111 | 0.1667 | 0 | 0 | 11290.39 | 2 | 4 |
| DeepSeek | `deepseek-v4-flash` | 3 | `complete_unstable` | 1.0000 | 0.0000 | 0.0000 | 0 | 7 | 17456.82 | 4 | 6 |
| Kimi | `kimi-latest` | 1 | `complete_unstable` | 0.0000 | 1.0000 | 1.0000 | 0 | 0 | 2458.00 | 0 | 0 |

## Tool error breakdown

| Provider | Validation | Harmless guard block | Budget | Runtime | Total |
|---|---:|---:|---:|---:|---:|
| OpenAI | 0 | 0 | 0 | 0 | 0 |
| Qwen | 0 | 0 | 0 | 0 | 0 |
| DeepSeek | 0 | 0 | 3 | 4 | 7 |
| Kimi | 0 | 0 | 0 | 0 | 0 |

Qwen had no tool errors on this run, but it is not strictly stable because
schema validity, fallback rate, and retry rate did not meet the
`complete_stable` threshold.

DeepSeek reached full schema validity with zero fallback and zero retry, but
tool budget/runtime errors keep it out of the strict stable bucket on this
fixture.

## Provider error classification

| Provider | Provider error classes |
|---|---|
| OpenAI | none |
| Qwen | `schema_error: 4`, `unknown_error: 1` |
| DeepSeek | none |
| Kimi | `unknown_error: 12` |

Kimi did not show a timeout in this single run, but the local model selection
used by `.env` was not accepted by the provider, so the result fell back for
all schema-validated agent calls. Previous connectivity checks showed the
Moonshot endpoint can work when the exact console-available model is used, but
Kimi remains unsuitable as a baseline until the model name is re-verified and
the fixture is re-run. The `kimi` profile keeps JSON mode disabled and does not
enable native tool calling.

## Current recommendation

- Recommended baseline for current SignalHarness eval: OpenAI `gpt-4o-mini`,
  because it is the only `complete_stable` provider in this run.
- Domestic candidate: Qwen `qwen-plus`, but not strictly stable on this
  fixture because retry/fallback were non-zero and schema validity was below
  the stable threshold.
- Smoke/low-cost candidate: DeepSeek `deepseek-v4-flash`, but treat it as less
  stable on this fixture because tool budget/runtime errors remain visible.
- Not recommended as the default yet: Kimi, because this run was unstable and
  should be repeated only after confirming the exact Moonshot model name
  available in the local console.

These are current local fixture results only. They should not be presented as a
universal model leaderboard.

## Limitations and next fixtures

- One fixture is not enough to benchmark model intelligence.
- The task is short and structured; long context, noisy source mixes, and
  conflicting evidence should be added next.
- Add fixtures for:
  - malformed or sparse GitHub events;
  - long RSS summaries and secondary-source contradiction;
  - multiple web-change sources with partial source failure;
  - high-risk learning proposal gates;
  - repair-triggering impact/action inconsistency.
