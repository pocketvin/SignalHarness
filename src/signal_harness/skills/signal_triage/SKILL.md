---
name: signal-triage
description: Classify a normalized project signal and select ignore, save, alert, or action_required.
---

# Signal Triage

## When to use

Use after a raw source record has been normalized into `SignalEvent`.

## Inputs

- `SignalEvent`
- project profile
- deterministic score breakdown

## Steps

1. Check explicit ignore keywords.
2. Identify dependency, competitor, policy, expert, team, or market context.
3. Use configured thresholds for the decision.
4. Preserve a short evidence-grounded reason.

## Output schema

Return category, decision, and reason fields compatible with `SignalAssessment`.

## Failure handling

If category evidence is insufficient, use `market_signal`; if provenance is
missing, lower confidence rather than inventing evidence.
