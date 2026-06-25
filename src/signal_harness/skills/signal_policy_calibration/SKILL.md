---
name: signal-policy-calibration
description: Analyze feedback history and propose reviewable policy adjustments.
---

# Signal Policy Calibration

## When to use

Use after feedback is recorded or when the user runs `signal-harness calibrate`.

## Inputs

- feedback history
- current signal policy

## Steps

1. Count useful, false-positive, missed, and generic judgments.
2. Propose small threshold or keyword adjustments.
3. Preserve historical assessments.
4. Write a `PolicyUpdateProposal`.
5. Require explicit user approval before changing YAML policy.

## Output schema

Return proposal ID, reason, old/new policy, changed keywords/sources, expected
effect, and `requires_approval: true`.

## Failure handling

When feedback volume is insufficient, preserve current thresholds and produce a
no-op proposal rather than guessing.
