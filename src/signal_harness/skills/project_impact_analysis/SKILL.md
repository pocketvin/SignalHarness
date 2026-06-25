---
name: project-impact-analysis
description: Map deterministic signal scores to project modules, risk, and priority.
---

# Project Impact Analysis

## When to use

Use after classification, evidence verification, and deterministic scoring.

## Inputs

- `SignalEvent`
- project profile
- `ScoreBreakdown`

## Steps

1. Match critical modules, dependencies, stack, competitors, and focus terms.
2. Preserve the deterministic relevance and final score.
3. Identify affected modules without claiming code-level changes.
4. Explain which structured factors drove priority.

## Output schema

Return relevance score, impact score, affected modules, and reason.

## Failure handling

If no module can be identified, return an empty list or `project-wide` only
when project relevance is high.
