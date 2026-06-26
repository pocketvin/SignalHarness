---
name: harness-eval
description: Evaluate routing, tools, guardrails, cache, clustering, and proposal safety.
---

## When to use

Use after changing prompts, schemas, routing, tools, memory, or permissions.

## Inputs

Scripted fixtures, MockProvider traces, assessments, proposal artifacts.

## Outputs

Route, noise, evidence, tool-block, fallback, and safety metrics.

## Steps

1. Run scripted mock cases without a real LLM key.
2. Verify route and evidence contracts.
3. Exercise allowed, blocked, failed, and cached tools.
4. Confirm proposals remain review-only.

## Constraints

Test workflow behavior, not model intelligence. Keep real Agent mode manual.
