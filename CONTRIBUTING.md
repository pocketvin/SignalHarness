# Contributing to SignalHarness

## Setup

```bash
uv sync --extra dev
```

## Required checks

```bash
uv run ruff check src tests scripts
uv run pytest -q
uv build
```

Changes to SignalHarness business behavior should include focused tests under
`tests/signal_harness/`.

## Design rules

- Keep core scores deterministic and expose their components.
- Preserve raw evidence and never fabricate source URLs.
- Route external capabilities through the SignalHarness Tool Registry.
- Do not add shell, edit, notebook, generic browser, or MCP tools to the
  SignalHarness allowlist.
- Generate policy proposals before applying configuration changes.
- Preserve `LICENSE`, `NOTICE.md`, and upstream attribution.
