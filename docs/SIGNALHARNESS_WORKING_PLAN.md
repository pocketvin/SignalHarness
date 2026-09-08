# SignalHarness Working Plan

> Status: Rolling construction plan
> Updated: 2026-09-09
> Governing target: `docs/SIGNALHARNESS_TARGET_STATE.md`

This document records where construction should go next and how each phase should be verified. It is intentionally mutable. After every completed phase, update Current State, Retrospective, Acceptance evidence, and the next phase before continuing.

## CURRENT STATE

Current repository baseline at planning time:

- branch: `main`
- planning baseline commit: `7e30625` — `Document SignalHarness target state and phased plan`.
- P1 and P2 implementations are complete locally; milestone Git/GitHub status is tracked by repository history and CI rather than transient text in this plan.
- existing product has project-scoped profile/watchlist state, GitHub release/issues, RSS, Web Snapshot/Diff, candidate funnel, deterministic scoring/guards, five-Agent runner, trace/evals, CLI/REST/SSE, Golden Demo, and five read-only MCP tools.
- project-scoped SQLite now persists EventRevision/Change/ProjectImpact/Scan/ScanChange plus window/checkpoint/source-coverage state; legacy YAML/JSON outputs remain compatibility projections.
- normalized/deduplicated candidates persist before Top-K; All Changes remain queryable independently of deep-analysis budget.
- GitHub release/issue collection now follows pagination with a bounded cap and explicit `partial/history_limited` coverage when the cap is reached.
- stream runs start on POST, persist queued/running input/status for bounded restart recovery, and use SSE only as an in-process observation/replay surface.
- current onboarding is review-required draft + explicit apply rather than auto-active profile.
- current real-agent scan already defers LearningPolicyAgent reflection from the critical path.

The existing five-Agent implementation is a baseline to preserve for comparison, not the future product contract.
## PHASE STRATEGY

Each phase is a bounded product capability, not a request to rewrite everything at once.

```text
P1 Persistent Change Ledger
P2 Scan Window & Durable Execution
P3 Project Profile + Preference Engine
P4 Analyzer/Harness V2 + Eval System
P5 Product Intelligence Experience
P6 Source Intelligence Expansion
P7 Continuous Monitoring
P8 Calibration Engine
```

The ordering expresses current dependency logic, not an immutable schedule. Construction evidence may trigger partial re-planning while preserving the confirmed target state.

## PHASE EXECUTION LOOP

For every phase:

```text
Target State
→ Construction Preflight
→ Skill Router
→ minimum baseline
→ one vertical slice
→ focused verification and repair
→ Integration Check
→ ChatGPT Web + Desktop Commander Acceptance
→ phase retrospective
→ Git/GitHub/CI completion when applicable
→ update this Working Plan
```

Do not use nested Codex completion reviewers for normal final acceptance in this project.
## P1 — Persistent Change Ledger

**Status: IMPLEMENTED / AGENT ACCEPTANCE PASS**

### Target

Changes collected from real sources must no longer disappear because of Top-K selection, a later run, or report failure. A user must be able to query all relevant Changes belonging to a Scan.

### Recommended approach

Start with the smallest relational model that supports the vertical slice. Current direction is SQLite + schema migration for local/single-owner deployment, but avoid prematurely creating every future table.

Initial capability should cover enough of:

- Event identity + EventRevision;
- stable Change + Change/Event evidence relation;
- project relevance/basic ProjectImpact state;
- Scan + ScanChange frozen membership;
- stable list/pagination query;
- compatibility projection to existing JSON/report paths where needed.

Persist observed/relevant data before deep-analysis Top-K selection. Existing five-Agent runner can remain the deep analyzer behind an adapter while the ledger is introduced.

### Acceptance

- controlled 300+ event replay does not lose relevant items when deep-analysis budget is 12;
- same source identity with changed content preserves old/new revisions;
- a report-stage failure cannot consume a Web Change so that retry sees zero;
- All Relevant Changes can be paginated from a frozen Scan;
- legacy output remains truthful and no legacy history is fabricated during migration.

### P1 implementation evidence

- Added project-scoped SQLite `change_ledger.sqlite3` with schema version metadata.
- Normalize/dedup output is persisted before Candidate Funnel Top-K.
- Event identity and EventRevision are separate; ScanChange pins the exact revision used by that Scan.
- `project_impacts` stores basic project-aware relevance for All, while analyzed items attach the existing assessment JSON.
- REST `GET /runs/{run_id}/changes?offset=&limit=` exposes frozen paginated ScanChange data.
- Legacy `signals.json` remains the deep-analysis shortlist; no legacy history is fabricated.
- Web Snapshot now uses pending-per-scan state and only promotes after report success.
- Regression includes 320 observed Changes with analysis budget 12, frozen revision history, report-failure retry, REST pagination, and existing compatibility paths.
- Fresh local verification: 229 tests PASS; Ruff PASS; strict mypy PASS (95 source files); regression-eval PASS; project-eval 3/3 PASS; `uv build` PASS.

### Explicitly defer

Do not redesign all Agents, add many new sources, or rebuild the frontend in P1.
## P2 — Scan Window & Durable Execution

**Status: IMPLEMENTED / AGENT ACCEPTANCE PASS**

### Target

Make `since_last`, 24h, 7d, 30d, custom windows, retries, and scheduled scans reliable and explainable.

### Recommended approach

Use frozen `[L,U)` Scan windows and separate interactive checkpoint, source cursor/coverage, schedule checkpoint, notification state, and Inbox read state.

Introduce persistent Scan/Job status with bounded retries and restart recovery. SSE becomes observation only; subscribing must not be what starts the real task.

Fix existing source completeness gaps while doing this vertical slice, especially GitHub pagination and truthful coverage diagnostics.

### Acceptance

- first-use `since_last` defaults to an explicit 7-day lookback;
- manual now-ending queries advance the interactive checkpoint; historical lookbacks and schedules do not;
- required-source partial failure does not silently skip the unresolved interval;
- late-discovered/revised items can appear later with explicit semantics;
- process restart recovers or safely retries the Scan;
- creating a Scan without opening SSE still runs it;
- source coverage and history limits are visible rather than inferred from HTTP success.

### P2 implementation evidence

- Added one frozen `ResolvedScanWindow` contract for `since_last`, 24h, 7d, 30d, custom, and legacy compatibility; source-timestamped facts use `[L,U)`.
- First-use `since_last` explicitly falls back to 7 days; later runs use a project/consumer interactive checkpoint stored separately from source/schedule/notification state.
- Only successful, checkpoint-eligible, interactive live scans advance the interactive checkpoint; fixture, historical custom, scheduled/non-interactive, and explicit partial/history-limited runs do not.
- Late-discovered and revised old facts can enter a later `since_last` once with explicit `late_discovery` / `late_revision` semantics; scan-local metadata does not create fake EventRevisions.
- Web Snapshot observations created during collection are explicitly tagged `observed_during_scan` rather than pretending to have a precise pre-U source timestamp.
- GitHub releases/issues follow pagination with a bounded page cap; reaching the cap produces visible `partial/history_limited` coverage instead of a false complete result.
- `scan_sources` persists per-source coverage, pages, history limits, and diagnostics; REST exposes `GET /runs/{run_id}/coverage`.
- `POST /stream-runs` now starts work immediately. Queued/running input and status are persisted and unfinished runs are boundedly recovered on service startup; SSE remains an in-process live/replay view, not a distributed durable queue.
- Fresh local verification: 242 tests PASS; Ruff PASS; strict mypy PASS (96 source files); regression-eval PASS; project-eval 3/3 PASS; `uv build` PASS; CLI window options smoke PASS.

## P3 — Project Profile + Preference Engine

### Target

A newly connected project becomes usable immediately, stays understandable over time, and gives the user a fast way to control what matters.

### Recommended approach

Introduce versioned Project Profile state plus explicit user overrides. Parse manifests/lockfiles and bounded repository facts without executing project code or reading secrets. Add structured importance controls and natural-language preference editing over the same underlying model.

Recommended quick-control scale: Critical / Important / Normal / Low / Ignore.

Start with the first real project's needs rather than generic support for every language/ecosystem.

### Acceptance

- project connection produces an auto-active profile without a mandatory review gate;
- ProfileRevision records purpose, stack, dependencies/providers/runtime/protocols/modules with evidence/unknowns;
- user can quickly change importance from the product UI and the change immediately affects later ranking/context;
- natural-language preference updates produce the same structured state;
- auto-refresh never overwrites explicit user overrides;
- historical Scan continues to reference its original ProfileRevision;
- project-local/private facts stay isolated by authorization scope.

### P3 implementation evidence

- SQLite Ledger schema v4 adds immutable `profile_revisions`, auditable/revocable `project_preferences`, and `scans.profile_revision_id`; P2 databases migrate in place and historical scans remain pinned to the revision they used.
- Effective Profile is computed as Auto Profile Facts + explicit active Preferences/Overrides. Auto refresh never deletes or overwrites the explicit preference rows.
- Five user importance levels are implemented: Critical / Important / Normal / Low / Ignore, scoped to dependency/provider/runtime/protocol/module/ecosystem/source/category/topic. Matching preferences affect deterministic relevance ranking and are also included in the effective Agent context.
- REST exposes project profile retrieval plus structured preference set/revoke and deterministic natural-language preference updates; the natural-language path maps into the same persisted preference model and does not call a paid model.
- `project-connect` CLI and `POST /projects/connect` perform safe manifest/lockfile inspection, register Project Catalog + Watchlist, create the first ProfileRevision, and make the project usable immediately. `project-draft` remains a compatibility preview path rather than a mandatory activation gate.
- Onboarding now accepts `uv.lock` / `package-lock.json` and records declared constraints, resolved versions, source files and confidence. The default SignalHarness profile carries verified dependency evidence from its own `pyproject.toml` + `uv.lock`.
- Golden Demo now shows the effective project profile and lets the user change entity importance directly or with natural-language input; browser directory connection sends only allowlisted manifests/lockfiles and relative paths, never source code or `.env`.
- Fresh local verification: 256 tests PASS; Ruff PASS; strict mypy PASS (97 source files); regression-eval PASS at decision/precision/recall 1.0; project-eval 3/3 PASS; `uv build` PASS.

## P4 — Analyzer/Harness V2 + Eval System

**Status: AGENT ACCEPTANCE PASS — offline Analyzer V2 candidate selected; live-provider comparison pending explicit cost authorization**

### Target

Determine which Agent/Harness components actually improve SignalHarness quality, instead of preserving the current five-Agent layout by habit.

### Recommended approach

Keep the current five-Agent runner as the baseline. Build a frozen evaluation corpus from deterministic edge cases plus a modest set of real Changes/Evidence. Compare harness variants on exactly the same inputs.

Suggested variants:

- current five-Agent runner;
- deterministic Supervisor/router + current downstream Agents;
- deterministic evidence resolution with no Evidence LLM call;
- selective EvidenceResearcher only for high-risk/direct-project cases;
- on-demand Evidence + merged ImpactActionAnalyzer;
- previous variant + SelectiveVerifier;
- optional episodic example retrieval when enough real examples exist.

Also compare SignalHarness against a **Generic LLM Monitor baseline**: give a strong model the same bounded external changes plus only a compact project description/preferences, without Change Ledger revision state, structured Project Profile facts, coverage/checkpoints, or historical ProjectImpact state. This baseline answers the product question “why not just schedule GPT to watch these sites?” rather than only comparing internal Agent layouts. Mock-provider results cannot prove this external baseline; use a modest frozen real-world corpus and a live-provider/manual quality run when credentials are explicitly available.

### Measure

Relevance recall, false positives/negatives, impact/module correctness, citation support, action usefulness, Top usefulness, latency, tokens, and cost. For the Generic LLM Monitor comparison also measure duplicate suppression, revision awareness, historical consistency, coverage truthfulness, and setup/context overhead.

### Acceptance

- all compared variants use frozen Change/Profile/Evidence/Preference inputs;
- analyzer/model/prompt/context/policy versions are recorded;
- a simpler variant is preferred when quality is equivalent or better;
- no component is kept solely because it looks more agentic;
- the final real-time Agent count remains an evidence-based result, not a precondition;
- the Generic LLM Monitor baseline is explicitly reported, and SignalHarness-specific complexity is retained only where it shows measurable project-intelligence value or durable-state/integration value the baseline does not provide.

### P4 first-slice implementation evidence

- Added versioned Harness variants and analyzer input fingerprints so compared runs record harness/analyzer/prompt/context/policy/provider/model versions on frozen semantic inputs.
- Compared five-Agent, deterministic Supervisor, deferred Learning, deterministic Evidence Resolver, and Selective EvidenceResearcher variants through `signal-harness harness-eval`.
- Frozen 40-case offline corpus result: five-Agent 6 LLM calls / 1.000 decision; deterministic Supervisor 5 / 1.000; deferred Learning 4 / 1.000; deterministic Evidence 2 / 0.975; selective EvidenceResearcher 3 / 1.000. Precision and recall remain 1.000 for all five variants on this corpus.
- Current recommendation is `selective-evidence-researcher`: it preserves baseline regression quality with half the LLM calls on the frozen mock-agent corpus. This is a project-specific ablation result, not a general live-model benchmark.
- Fixed the ablation non-inferiority gate to require every guarded quality metric to match/beat baseline independently rather than using lexicographic tuple comparison; losing experimental variants no longer invalidate an otherwise sound ablation run.
- Focused Harness tests PASS; harness-eval `--enforce` PASS; regression-eval PASS at decision/precision/recall 1.0; project-eval PASS 3/3.
- Fresh full verification after both P4 slices: 260 tests PASS; Ruff PASS; strict mypy PASS (100 source files); harness-eval PASS; regression-eval PASS; project-eval 3/3 PASS; `uv build` PASS.
- Added `signal-harness generic-monitor-eval` as the external product baseline harness. It gives one live model only a compact project brief plus the frozen event corpus, performs one structured LLM call, and records schema validity, prompt size, tokens, estimated cost, and the same labelled regression metrics.
- The compact baseline brief intentionally excludes dependency-version evidence, critical-module details, Change Ledger/revision state, coverage/checkpoints, tools, and historical ProjectImpact state; focused tests prove those deeper structured facts do not leak into the baseline context.
- Generic-monitor plumbing tests PASS and the CLI help path is usable. No live-provider comparison was executed in this slice because that would incur external model cost/credentials; the live measurement remains environment/owner-controlled evidence.
- Added a 15-case frozen real-world 2026-Q3 corpus built from current upstream release/spec/repository facts plus explicit project-state labels. It includes hard negatives for already-installed/superseded versions and for ecosystem releases that merely mention a direct dependency.
- The first real-world run exposed a shared deterministic-layer failure rather than an Agent-count problem: all variants started at 0.400 decision accuracy with priority recall 0.000. Source-identity matching, resolved-version applicability, protocol identity, and project-specific risk floors were then moved into a shared deterministic `ProjectChangeState` layer.
- After that repair, the same real-world corpus reaches decision/precision/recall 1.000 across all compared Harness variants while the original 40-case corpus remains regression-clean. Direct dependencies are now resolved from source/package identity rather than body mentions, and installed/superseded/fixed-on-current-major changes can be conservatively ignored.
- Added `selective-evidence-impact-action`: deterministic Supervisor + deterministic evidence resolution + selective EvidenceResearcher + one merged ImpactActionAnalyzer call. On the original 40-case corpus it matches five-Agent decision/precision/recall 1.000 with 2 LLM calls instead of 6; the 15-case real-world corpus is also 1.000/1.000/1.000 with 2 calls.
- Added `selective-evidence-impact-action-verifier` as an explicit SelectiveVerifier ablation. It also preserves quality but requires 3 LLM calls and provides no measured gain on either frozen corpus, so SelectiveVerifier is not recommended for the Scan hot path at this stage. Verifier outputs are constrained so Python can only apply conservative confidence/relevance/risk/action reductions.
- Pure deterministic Evidence also uses 2 calls and passes the real-world corpus, but remains 0.975 decision accuracy on the original corpus. Across both corpora, the robust offline candidate is therefore `selective-evidence-impact-action`, not fully deterministic Evidence.
- The existing five-Agent default remains protected for real-provider runs until a live-provider/manual quality smoke is explicitly authorized; changing that default without live-model evidence would overstate what the offline scripted provider proves.
- Generic Monitor live comparison remains environment/owner-controlled because it invokes a paid external model. Its harness is implemented and ready, but no external model cost was incurred automatically.

## P5 — Product Intelligence Experience

**Status: IMPLEMENTED / AGENT ACCEPTANCE PASS**

### Target

Turn durable intelligence into the actual user product: overall report first, compact Top changes second, complete relevant history behind it, and the same result contract through all interfaces.

### Recommended approach

Add versioned product projections over Scan data:

- overall Chinese environment report generated from All Relevant Changes;
- Top roughly 10–15 selected by policy/ranking without deleting All;
- compact change list with type / summary_zh / one-line project impact;
- detail view with what/why/modules/actions/Before-After/evidence/audit;
- All Relevant Changes with filters, search, stable pagination, and sorting;
- shared Markdown renderer for overall report, Top set, and single Change;
- one core Scan application service used by Web/CLI/REST/MCP;
- CLI as the canonical developer + shell-capable coding-Agent interface, with stable machine-readable JSON for project/scan/change/report operations and clean stdout/stderr separation;
- MCP kept thin and optional, delegating to the same application services rather than duplicating business logic.

MCP may add a truthful fresh-scan tool plus status/result/list/detail tools where MCP clients benefit from schema/tool discovery. Long scans return a persistent handle rather than depending on one request staying open. Shell-capable Agents must not need MCP merely to access the same intelligence.

### Acceptance

- a real project query produces overall report → Top → All → detail as one coherent Scan;
- changing Top count does not change All count;
- report statistics/themes reflect the full relevant set, not only Top;
- pagination remains stable while new scans occur;
- three copy/export modes produce clean Markdown without default engineering Trace noise;
- MCP can trigger a fresh Scan when no prior result exists and later retrieve it;
- CLI/Web/REST/MCP projections agree on the same scan_id and product data when those interfaces expose the operation;
- core CLI JSON paths are usable without scraping human-formatted terminal tables.

### P5 first-slice implementation evidence

- Added `ProductIntelligenceService` as the shared read-model over frozen `Scan` / `ScanChange` ledger state; no new persistence schema was required for this slice.
- One product contract now exposes Overall Report → Top Changes → All Relevant Changes → Change Detail. Top count is presentation-only and cannot change All membership/count.
- Overall report statistics/themes are computed from the full frozen Scan projection. Unanalyzed Changes are represented truthfully rather than being silently dropped or given fabricated deep-impact conclusions.
- All Relevant Changes supports stable offset pagination, search, analysis status filters, decision/source/category filters, and rank/impact/newest sorting. Detail exposes what changed, project relevance, affected modules, actions, evidence, real Before/After when present, and bounded audit metadata.
- Shared Markdown rendering supports report, Top, and single-Change export without default Trace engineering noise.
- CLI-first paths are implemented: `scan --json`, `report --json`, `changes --json`, `change --json`, and `export`. JSON requested data stays on stdout; machine-readable failures use stderr and non-zero exit codes.
- REST delegates to the same product service through `GET /runs/{run_id}/product`, `/report`, `/changes`, and `/changes/{change_id}`. Focused acceptance proves CLI and REST return the same scan id, report stats, and Top IDs under the same Top budget.
- Golden Demo now consumes the Product Intelligence contract after a run and presents Profile → Overall Report → Top → All → Detail → Agent Audit. Legacy `signals` / `assessments` remain a compatibility fallback/audit source, not the primary product view.
- Fresh local acceptance: 274 tests PASS; `git diff --check` PASS; JavaScript syntax PASS; Ruff PASS; strict mypy PASS (104 source files); CLI mock-agent JSON/read/export + trace/calibrate smoke PASS; REST/Web product smoke PASS; `uv build` PASS.
- Thin MCP adapter is complete: 10 tools total, with 9 read-only retrieval/context tools plus non-idempotent `signalharness_start_scan`. MCP fresh scans reuse the same persistent `StreamRunManager`; status/product/list/detail delegate to the same product/run state rather than duplicating business logic.
- MCP acceptance covers persistent fixture scan start → status → product/list/detail, rejected out-of-scope fixture paths, truthful tool annotations, and completed-run reads after rebuilding the MCP server object. FastAPI and MCP share one manager in service mode.
- Final fresh P5 local acceptance: 277 tests PASS; Ruff PASS; strict mypy PASS (104 source files); JavaScript syntax PASS; `uv build` PASS; regression-eval decision/precision/recall 1.000/1.000/1.000; project-eval 3/3 PASS. A full visual/human browser polish pass remains optional owner acceptance, not a blocker for objective P5 completion.
## P6 — Source Intelligence Expansion

### Target

Expand environmental coverage only through connectors that have clear identity, revision, coverage, and project-relevance semantics.

### Recommended order

1. own-project commits/merged PR/local Git where not already complete;
2. the first real project's most important package registry (PyPI or npm first);
3. OSV/security matching against actual dependency versions;
4. critical provider/API/runtime/spec changelogs and official feeds;
5. selected ecosystem sources with lower default authority.

Do not treat “added a tool name” as connector completion. Every source needs pagination/history limits, retries/rate-limit behavior, revision semantics, provenance, and truthful partial coverage.

### Acceptance

- registry release + GitHub release/changelog can aggregate into one Change when they represent one release;
- affected and unaffected dependency versions are distinguished for security analysis;
- provider/spec changes point to actual project usage when evidence exists and weaken claims when usage is uncertain;
- each connector has deterministic failure tests plus at least one real-source smoke;
- untrusted source content cannot gain additional tool or permission authority.
## P7 — Continuous Monitoring

### Target

SignalHarness runs without manual prompting and delivers important intelligence without corrupting manual query semantics.

### Recommended approach

Build persistent Schedule state on top of the same Scan service, then add Inbox, notification policy, Outbox, DeliveryAttempt, retry/cooldown/digest behavior, and one real external delivery path.

Use a mature scheduler trigger implementation where useful; keep business idempotency and Scan state in SignalHarness. Start with one delivery path such as signed Webhook or ntfy; add Apprise only when broad channel support becomes a real requirement.

### Acceptance

- 12h / 24h / local-time schedules generate correct windows;
- restart/missed-run behavior is defined and tested;
- scheduled runs do not advance manual `since_last`;
- delivery retries do not create duplicate logical notifications;
- revision/importance escalation can intentionally create a new notification;
- one real notification reaches a real destination and links back to a durable Scan/Report;
- local alert files are treated as artifacts, not external-delivery proof.
## P8 — Calibration Engine

### Target

Use real feedback and outcomes to improve SignalHarness safely, without letting a Scan-time Agent rewrite its own rules.

### Recommended approach

Prepare the data model earlier (feedback, outcome, analyzer/policy revisions), but do not build a heavy learning system before there is real usage data.

When enough evidence exists, use:

```text
Feedback / Outcome
→ Episode
→ Consolidation
→ Candidate Preference/Policy
→ Historical Replay
→ Shadow Evaluation
→ Proposal
→ Versioned Promotion
```

Explicit preferences remain separate from learned suggestions. Procedural-policy changes require stronger replay/eval gates than low-risk project preference suggestions.

### Acceptance

- calibration uses real stored feedback/outcomes rather than fabricated “learning success”;
- candidate changes can be replayed against frozen historical inputs;
- shadow results show what ranking/notification behavior would have changed before activation;
- active policy/preference revisions remain versioned and reversible;
- a candidate that does not improve agreed metrics is rejected;
- Scan-time analysis remains available even when Calibration is disabled.
## CONFIRMED DECISIONS

The following are not open implementation debates unless the user changes product direction:

- SignalHarness is Project Environment Intelligence, not a fixed Agent-Harness product.
- Event, Change, and ProjectImpact are distinct business concepts.
- Top 10–15 never determines whether relevant Changes are persisted/queryable.
- the overall Chinese report is based on All Relevant Changes, not just Top.
- explicit user preference has higher authority than model inference or learned suggestion.
- user needs dedicated fast importance controls in addition to chat/natural-language editing.
- `since_last`, source cursor, schedule checkpoint, notification state, and read state remain separate.
- scheduled scans do not silently consume manual `since_last`.
- public world facts may be reused, but Profile/ProjectImpact/Preference/Feedback/query state remain project-scoped.
- the deterministic runtime owns permissions, persistence success, transaction boundaries, budgets, and authoritative policy/scoring decisions.
- Learning/Calibration is not a required fifth Agent call in the Scan hot path.
- normal acceptance is performed by ChatGPT Web + Desktop Commander, not nested completion reviewers.

## CURRENT ARCHITECTURE DIRECTIONS

- modular monolith rather than microservices;
- CLI-first for developers and Shell-capable coding Agents; MCP remains a thin optional adapter rather than the product core;
- relational durable state as product needs justify it; SQLite is the first local/single-owner candidate;
- Brain / Session / Hands separation;
- bounded ContextPacketBuilder;
- deterministic outer pipeline with local bounded Agent loops;
- current five-Agent implementation retained as a baseline until P4 evaluation;
- mature notification/scheduling components preferred over building every transport/runtime from scratch.
## EXPERIMENTAL / TO VALIDATE

Do not freeze these prematurely:

- final real-time Agent count;
- whether LLM Supervisor survives vs deterministic triage;
- whether ActionPlanner stays separate vs merges into ImpactActionAnalyzer;
- how often EvidenceResearcher should run;
- whether SelectiveVerifier materially improves high-risk/low-confidence cases;
- whether episodic retrieval needs embeddings/vector search or simpler indexed retrieval;
- exact ranking formula and learned preference weights;
- model/provider/prompt choice;
- SQLite scaling point and whether a future deployment requires PostgreSQL;
- whether a heavier durable workflow runtime is ever justified.

## OPEN QUESTIONS FOR LATER PHASES

These are intentionally deferred until the relevant phase because they do not block P1:

- first real trial project(s) and first registry/provider priorities;
- exact profile source-code excerpt policy for external model calls;
- exact user-facing semantics for optional-source partial coverage;
- first external notification destination;
- retention/export/deletion policy for evidence and historical reports;
- future local-only vs hosted/multi-user deployment boundary.

## NEXT ACTION

**Proceed to P6 — Source Intelligence Expansion.**

P5 is agent-accepted across the shared Product Intelligence core, CLI-first JSON interface, REST, Golden Demo, and thin MCP adapter. Preserve CLI as the primary developer/shell-capable Agent surface and keep MCP thin. Next, expand sources in evidence order: own-project Git facts where incomplete, then the first high-value package-registry connector, followed by OSV/security version matching. Do not add a connector unless identity, revision, pagination/history limits, provenance, failure semantics, and project-version applicability are testable.

## RETROSPECTIVE TEMPLATE

After each phase, replace/update this section with fresh evidence:

- Phase completed:
- Target State reached:
- Key implementation decisions:
- What changed from the original approach and why:
- Agent-verifiable Acceptance:
- Environment-dependent Acceptance:
- Owner-only/Pilot items:
- Regression / real-source evidence:
- Git/GitHub/CI state:
- New facts affecting future phases:
- Next phase / revised priority: