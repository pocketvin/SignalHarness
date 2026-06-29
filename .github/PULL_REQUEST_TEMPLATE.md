## Summary

- What problem does this PR solve?
- What changed?

## Validation

- [ ] `uv run --extra dev python -m pytest tests/signal_harness -q`
- [ ] `uv run --extra dev ruff check src/signal_harness tests/signal_harness`
- [ ] `uv run --extra dev mypy src/signal_harness`
- [ ] `uv run signal-harness scan --fixture examples/signal_harness/sample_events.json --mode mock-agent`
- [ ] `uv build`

## Notes

- Related issue:
- Follow-up work:
