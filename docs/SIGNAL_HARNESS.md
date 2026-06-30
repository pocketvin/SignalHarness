# SignalHarness Product and Architecture

SignalHarness is a standalone LLM-enhanced, project-specific signal
intelligence harness.
Its primary path uses five LLM Agents; deterministic Python remains the
guardrail, fallback, permission, score, replay, persistence, and trace layer.

## Core workflow

1. Load project, watchlist, policy, signal, and feedback memory.
2. Collect and normalize GitHub, RSS, and web-change events.
3. Deduplicate events.
4. Run SignalSupervisor, ContextEvidence, ImpactAnalyst, and ActionPlanner.
5. Calculate the deterministic base and guarded blended score.
6. Check requested actions with `SignalPermissionGuard`.
7. Write reports and complete LLM traces.
8. Run LearningPolicyAgent reflection without applying changes.

## Runtime foundation

SignalHarness uses local tool contracts, a small provider protocol, permission
guards, deterministic persistence, and explicit trace records. `providers/` is
a thin adapter layer around the mock provider and the OpenAI-compatible HTTP
provider; it is not a replacement runtime for a general Agent framework.

## Commands

```bash
signal-harness scan --mode demo|mock-agent|agent [--fixture FILE]
signal-harness report
signal-harness trace
signal-harness feedback --signal-id ID --label LABEL [--note TEXT]
signal-harness calibrate --mode demo|mock-agent|agent [--apply] [--yes]
```

The project does not automatically edit code, open pull requests, create
issues, send notifications, or apply learning proposals.
