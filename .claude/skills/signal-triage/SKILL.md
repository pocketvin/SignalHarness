---
name: signal-triage
description: Route normalized project signals to the minimum required Agent stages.
---

## When to use

Use after normalization, deduplication, noise hints, and clustering.

## Inputs

Signal events, project profile, noise assessments, related clusters.

## Outputs

Category, analyze flag, required Agents, reasons, cluster ID.

## Steps

1. Check explicit noise evidence.
2. Classify against project dependencies and concerns.
3. Select only required evidence, impact, action, and learning stages.
4. Explain overrides of deterministic noise hints.

## Constraints

Do not score, perform evidence research, create actions, or update policy.
