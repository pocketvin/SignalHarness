# Scheduled Runs

SignalHarness core is a one-shot scan engine. Scheduling is intentionally left
to the platform around it: GitHub Actions, cron, launchd, or systemd timers.

There is no built-in daemon because external schedulers are easier to deploy,
debug, disable, and explain in an interview. This also avoids Redis, Celery,
APScheduler, Postgres, and long-running service state.

## Common commands

```bash
# Offline scripted architecture demo
signal-harness scan \
  --fixture examples/signal_harness/sample_events.json \
  --mode mock-agent

# Deterministic fallback scan
signal-harness scan \
  --fixture examples/signal_harness/sample_events.json \
  --mode demo

# Optional real-provider path
LLM_API_KEY=... signal-harness scan --mode agent
```

## State and outputs

Scheduled runs update local files:

- `.signal-harness/cache/`
- `.signal-harness/signal_memory.json`
- `.signal-harness/latest_learning_observation.json` for mock-agent/agent
- `.signal-harness/alert_state.json`
- `outputs/`
- `outputs/alerts.json`
- `outputs/alerts.md`
- `outputs/dashboard.html`

The default alert dispatcher writes local files only. The LLM does not send
notifications.

## Examples

- GitHub Actions: `examples/scheduled/github-actions.yml`
- cron: `examples/scheduled/crontab.example`
- launchd: `examples/scheduled/launchd.example.plist`

