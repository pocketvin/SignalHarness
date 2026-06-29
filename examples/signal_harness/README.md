# SignalHarness Example Fixtures

These fixtures support offline demos, scripted evals, and public-safe CI. They
do not require network access or real API keys.

## Files

- `sample_events.json`: small four-event fixture for `demo` and `mock-agent`
  scans.
- `eval_multisource_events.json`: multi-source fixture for evidence,
  clustering, controlled tool-use, and eval demonstrations.
- `project_profile.yaml`: demo project context used by scoring and prompts.
- `signal_policy.yaml`: deterministic scoring thresholds, category weights,
  and feedback adjustments.
- `watchlist.yaml`: example GitHub/RSS/web-change source configuration.

## Recommended commands

```bash
uv run signal-harness scan \
  --fixture examples/signal_harness/sample_events.json \
  --mode demo

uv run signal-harness scan \
  --fixture examples/signal_harness/sample_events.json \
  --mode mock-agent

uv run signal-harness trace
uv run signal-harness dashboard
uv run signal-harness digest --period daily
```

## Expected local outputs

After a scan, inspect:

- `outputs/signals.json`
- `outputs/impact_scores.json`
- `outputs/action_items.json`
- `outputs/task_trace.json`
- `outputs/trace_summary.md`
- `outputs/alerts.json`
- `outputs/alerts.md`
- `outputs/dashboard.html`
- `outputs/radar_digest.md`
- `outputs/run_summary.txt`

After calibration, inspect:

- `.signal-harness/latest_learning_observation.json`
- `.signal-harness/policy_update_proposal.json`
- `.signal-harness/skill_update_proposal.md`
- `.signal-harness/watchlist_update_proposal.json`
- `.signal-harness/replay_evaluation.json`
- `outputs/latest_policy_update_proposal.json`
- `outputs/latest_skill_update_proposal.md`
- `outputs/latest_watchlist_update_proposal.json`
- `outputs/latest_replay_evaluation.json`

The `outputs/latest_*` files are demo snapshots only. They do not mean that a
proposal was applied.
