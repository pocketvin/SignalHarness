# SignalHarness Evaluation System

SignalHarness has two evaluation layers with different purposes. They should not be mixed into one benchmark claim.

## 1. Agent regression gate

`regression-eval` tests product behaviour against a labelled, project-specific corpus.

```bash
uv run signal-harness regression-eval \
  --mode mock-agent \
  --enforce
```

The committed `resume-v1` suite contains 40 cases under:

- `examples/signal_harness/regression_events.json`
- `examples/signal_harness/regression_expectations.json`

It covers high-risk dependency changes, routine releases, negated risk language, policy/tool/provider/schema signals, RSS/source noise, competitors, web changes, and explicit irrelevant noise.

The suite runs through the real five-Agent runner with the scripted offline provider. It is deterministic enough for public CI while still exercising routing, evidence planning, guarded scoring, permissions, and final decision logic.
## Current committed regression evidence

After fixing the issues exposed by the first regression run, the current suite passes:

```text
cases                  40 / 40 assessed
decision accuracy      1.0000
category accuracy      1.0000
priority precision     1.0000
priority recall        1.0000
false-positive rate    0.0000
false-negative rate    0.0000
```

The initial baseline was materially worse: decision accuracy 0.7500 and priority recall 0.2857. The regression corpus exposed three general defects: category weighting was applied twice in the Agent scoring path; mock-Agent routing was missing stable project context; and negated risk phrases such as `no breaking change` still triggered positive risk terms.

The fixes were architectural rather than case-ID special cases: category weight is now applied once at the guarded blend boundary, text semantics are shared and negation-aware, project context is wired into mock routing, and official vulnerability/supply-chain signals have a Python-owned minimum alert floor.

`--enforce` returns a non-zero exit code when the configured gate fails, so the suite is a real CI regression gate rather than a report-only script.

## Regression metrics

The summary includes exact decision/category accuracy, priority precision and recall, FPR/FNR, TP/FP/TN/FN, confusion counts, missing assessments, and per-case mismatches.
## 2. Provider contract eval

`model-eval` answers a separate question: can a provider participate safely in the SignalHarness structured Agent contract?

```bash
uv run signal-harness model-eval \
  --fixture examples/signal_harness/sample_events.json \
  --mode mock-agent \
  --runs 2
```

It measures schema validity, retries, deterministic fallback, timeouts, tool-plan validity, validation/block/budget/runtime tool errors, repair behaviour, decision counts, run-state isolation, latency, provider-reported token usage, and estimated cost when the model profile contains pricing metadata.

For `--runs N` greater than one, state is isolated per run so memory from an earlier run cannot silently affect a later comparison.

Mock runs do not fabricate token usage. Real-provider token counts come from the provider response. Cost is an estimate calculated only when pricing metadata is configured for the selected model profile.

Historical real-provider snapshots live in `docs/MODEL_EVAL_REPORT.md`. Those numbers are evidence from their recorded date, not a current universal model ranking.

## Evaluation boundary

Neither eval claims general LLM intelligence. Regression eval proves behaviour for SignalHarness product contracts; provider eval proves compatibility and operational stability inside this Harness. Live model quality still depends on provider version, prompt changes, network conditions, source mix, and API configuration.
