---
name: evidence-verification
description: Plan safe read-only lookups and synthesize evidence from observations.
---

## When to use

Use for routes requiring `context_evidence`.

## Inputs

Events, clusters, source diversity, routes, tool observations.

## Outputs

Primary URLs, source quality, confidence, unsupported claims, uncertainty.

## Steps

1. Propose only allowlisted read-only tool requests.
2. Review runner-provided observations.
3. Prefer primary sources and identify conflicts.
4. Lower confidence when lookups fail or evidence is weak.

## Constraints

Never invoke tools directly, decide final impact, or invent sources.
