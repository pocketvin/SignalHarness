# SignalHarness Agent Guide

SignalHarness now has a direction-first product path plus a protected legacy multi-agent
baseline. The Web product is NOT the legacy Top-K Agent runner.

For future product direction and construction planning, `docs/SIGNALHARNESS_TARGET_STATE.md`
and `docs/SIGNALHARNESS_WORKING_PLAN.md` take precedence over older fixed-five-Agent
product assumptions. The existing five-Agent implementation remains a protected current-state
baseline until an evidence-based Analyzer/Harness migration is implemented and verified.

Key directories:

- `intelligence/`: full-corpus interpretation, ONE global synthesis, lazy clicked Deep Dive
- `persistence/intelligence.py`: additive versioned intelligence in the existing Ledger
- `service_intelligence.py`: model-free product request contract
- `providers/task_policy.py` + `configs/intelligence_policy.yaml`: backend model routing
- `agent_team/`: legacy domain LLM Agents for regression/compatibility
- `agent_integration/`: prompts, schemas, context, runner, trace
- `providers/`: scripted mock adapter and optional provider integration
- `memory/`: Project, Signal, Feedback, and Policy infrastructure
- `signal/`: deterministic normalization, noise, clustering, scoring
- `runtime/`: workflow, cache, permissions, tools, trace
- `frontend/src/environment/`: primary direction-first product source for `/demo`;
- `frontend/`: React + TypeScript source; Vite compiles into `src/signal_harness/ui/static/demo.*`
- `ui/static/`: package-owned compiled Demo assets plus the separate Narrative review static surface

Run with:

```bash
# Product path (live-provider credentials required, never run in offline CI):
uv run signal-harness environment --project signalharness --window since_last
uv run signal-harness environment-report --project signalharness
# Legacy/offline baseline:
uv run signal-harness scan --mode demo|mock-agent|agent
uv run signal-harness trace
uv run signal-harness calibrate --mode mock-agent
uv run signal-harness serve --host 127.0.0.1 --port 8000  # /demo + REST/SSE + /mcp
npm --prefix frontend ci --ignore-scripts
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Rules:

- New product scans must process every Change through bounded shallow batches, then ONE global model call. Never use Featured or Top-K as the global model's corpus.
- Project-owned Changes are still shallowly interpreted but may only inform project-connection context; they must never support an external EnvironmentDirection/Brief/Featured item.
- GitHub Issue/discussion evidence must retain a reported/discussed posture unless stronger release/official evidence independently establishes shipped behavior. Do not weaken Direction diversity/temporal/overlap guards merely to obtain a non-empty report.
- No automatic evidence-heavy Deep Dive in Scan. Only explicit POST/click starts it; GET/hover do not. Deep Dive must not overwrite a Scan/Profile/Report snapshot.
- Production provider/runtime modules must not import capability_eval or other eval implementations. Shared data contracts belong outside eval.
- Model selection belongs to the backend task policy; do not add model/mock/score controls back to the normal Web UI.
- The source/ID/Chinese guards do not prove semantic truth. Mark partial/unavailable analysis visibly; never fill it with fake successful model output.
- Before claiming full acceptance, distinguish 300-item offline contract tests from real-provider replay and from actual live-source collection. Keep real/model smoke artifacts isolated under project work/outputs.

- Do not add LangGraph, CrewAI, AutoGen, Redis, Postgres, Celery, VectorDB, or
  embedding databases.
- Do not describe deterministic fallback as true multi-Agent execution.
- Memory is infrastructure, not an Agent.
- `demo` is deterministic fallback; `mock-agent` uses scripted offline LLM-like
  calls; `agent` uses an optional real-provider integration.
- Keep provider adapters thin; demo, mock-agent, and public CI must not require
  upstream framework imports.
- Prompt, schema, route, or tool-use changes require matching tests and docs.
- `/demo` UI changes must be made in `frontend/src/`, never by hand-editing minified `ui/static/demo.js` or generated `demo.css`. Run the Vite build to refresh the package-owned compiled assets.
- Keep FastAPI/SSE/Product contracts independent from React; the frontend consumes existing REST/SSE APIs rather than duplicating scoring, routing, scheduling, or persistence logic.
- Public CI must stay SignalHarness-focused and offline: pytest
  `tests/signal_harness`, Ruff, mypy, and `uv build` only. It must not require
  `LLM_API_KEY`, run `--mode agent`, or call live providers. Real API smoke
  tests are documented, manual, and require explicit environment variables.
- Never commit hardcoded API keys, secret-looking fallback credentials, `.env`,
  runtime outputs, caches, or disposable build artifacts. The compiled `ui/static/demo.*` files are an intentional package/distribution asset and must stay synchronized with `frontend/src/`.

Before handoff:

```bash
npm --prefix frontend ci --ignore-scripts
npm --prefix frontend run typecheck
npm --prefix frontend run build
uv run --extra dev python -m pytest tests/signal_harness -q
uv run --extra dev ruff check src/signal_harness tests/signal_harness
uv run --extra dev mypy src/signal_harness
uv run signal-harness scan --fixture examples/signal_harness/sample_events.json --mode mock-agent
uv run signal-harness trace
uv run signal-harness calibrate --mode mock-agent
uv build
```
