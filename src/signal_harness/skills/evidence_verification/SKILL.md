---
name: evidence-verification
description: Verify signal provenance and prevent unsupported agent conclusions.
---

# Evidence Verification

## When to use

Use for every normalized signal before impact analysis.

## Inputs

- source type
- source name
- primary URL
- raw source payload

## Steps

1. Prefer official repository or publisher URLs.
2. Distinguish official, secondary, community, and unverified sources.
3. Return only URLs present in the event.
4. Lower confidence when the primary URL is absent.

## Output schema

Return `evidence_urls`, `source_quality`, `confidence`, and `reason`.

## Failure handling

Mark the source `unverified` and keep confidence below 0.5. Never fabricate a
source URL.
