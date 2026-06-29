# Upstream Attribution

SignalHarness originated from downstream work on
[HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness). The project now aims
to stand on its own as a focused signal-intelligence harness, while preserving
MIT attribution for reused or adapted upstream ideas and code.

## What came from OpenHarness

The original upstream work influenced or provided starting points for:

- tool abstraction and registry patterns;
- permission and guardrail patterns;
- provider/client integration ideas;
- atomic local persistence practices;
- CLI/runtime structure;
- skill and prompt organization concepts.

SignalHarness has migrated the small interfaces it needs into local modules so
demo and mock-agent modes do not depend on the full upstream package.

## Why upstream entry points were removed

The `openharness`, `oh`, `openh`, and `ohmo` scripts were removed from
SignalHarness packaging to avoid presenting this repository as an OpenHarness
distribution. The public command is now only:

```bash
signal-harness
```

This keeps a job-interview or portfolio demo focused on the SignalHarness
problem: project-environment signal monitoring, evidence verification, impact
analysis, safe action planning, trace/eval, and review-only learning proposals.

## License and notice

The MIT license is preserved in `LICENSE`, and `NOTICE.md` documents the
OpenHarness origin. SignalHarness-specific workflows, schemas, scoring,
memory, outputs, and documentation are part of this downstream project.

## Current relationship

OpenHarness is now an attributed origin and optional integration influence, not
the public identity of the repository. SignalHarness core remains local-first,
offline-testable, and independent of upstream UI, channel, swarm, ohmo, MCP,
and autopilot modules.

