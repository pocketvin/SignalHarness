# SignalHarness Model Eval Report

Last updated: 2026-07-01 01:12 CST

This report records a current local fixture result. It is not a permanent model
ranking and should be re-run when fixtures, prompts, provider models, or API
settings change.

## Setup

- Fixture: `examples/signal_harness/sample_events.json`
- Mode: `agent`
- Matrix command:
  - `bash scripts/model_eval_matrix.sh --providers openai,qwen,deepseek --runs 3`
  - `bash scripts/model_eval_matrix.sh --providers kimi --runs 1 --sleep 10`
- Provider path: SignalHarness OpenAI-compatible HTTP adapter
- Tool loop: runner-controlled `EvidenceToolPlan` → Python validation/execution
  → `ToolObservation` → final evidence
- CI impact: none. Public CI still uses offline mock-agent/scripted eval only.

## Current local results

| Provider | Model | Runs | Schema valid | Fallback | Retry | Timeout | Avg latency ms | Repair requested | Repair executed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| OpenAI | `gpt-4o-mini` | 3 | 1.0000 | 0.0000 | 0.0000 | 0 | 5807.00 | 0 | 0 |
| Qwen | `qwen-plus` | 3 | 1.0000 | 0.0000 | 0.0833 | 0 | 13649.08 | 0 | 0 |
| DeepSeek | `deepseek-v4-flash` | 3 | 0.9500 | 0.0500 | 0.1500 | 0 | 23256.05 | 2 | 2 |
| Kimi | `kimi-k2.7-code` | 1 | 0.0000 | 1.0000 | 0.8333 | 1 | 16427.67 | 0 | 0 |

## Tool error breakdown

| Provider | Validation | Harmless guard block | Budget | Runtime | Total |
|---|---:|---:|---:|---:|---:|
| OpenAI | 0 | 0 | 0 | 0 | 0 |
| Qwen | 0 | 0 | 0 | 0 | 0 |
| DeepSeek | 0 | 0 | 0 | 2 | 2 |
| Kimi | 0 | 0 | 0 | 0 | 0 |

The tool prompt change dropped Qwen and DeepSeek tool validation errors to zero
on this fixture. Missing `action`, `repo`, `url`, or `fixture` would be counted
as a model tool argument adherence issue. Blocked-tool counts would represent
normal harmless guardrail behavior when Python rejects non-allowlisted or
non-read-only requests.

DeepSeek still showed two aggregated runtime tool errors. These were executor
or source failures, not schema/tool-argument failures. The latest per-provider
trace only preserves the final run, while `model_eval_summary.json` aggregates
all three runs.

## Provider error classification

| Provider | Provider error classes |
|---|---|
| OpenAI | none |
| Qwen | `schema_error: 1` |
| DeepSeek | `schema_error: 2`, `unknown_error: 2` |
| Kimi | `http_error: 10`, `timeout: 2` |

Kimi did not fail because of 429 in this run. The direct visible failure is
HTTP 400 from `https://api.moonshot.cn/v1/chat/completions`, plus one provider
timeout. SignalHarness does not store raw provider responses in committed docs,
so the safe conclusion is that the configured Kimi model/endpoint/payload
combination is incompatible for this run. Re-test with the exact model name
available in the Moonshot console, for example `kimi-latest` if that is the
account-supported model. The `kimi` profile already keeps JSON mode disabled.

## Current recommendation

- Recommended baseline for current SignalHarness eval: OpenAI `gpt-4o-mini`.
- Domestic/default candidate: Qwen `qwen-plus`, because it reached full schema
  validity and zero tool errors on this fixture, with higher latency and one
  schema retry.
- Smoke/low-cost candidate: DeepSeek `deepseek-v4-flash`, but treat it as less
  stable on this fixture because fallback, retries, runtime tool errors, and
  higher latency remain visible.
- Not recommended until retested: Kimi `kimi-k2.7-code`, because the endpoint
  returned HTTP 400 and all Agent calls fell back.

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
