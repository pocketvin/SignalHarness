# SignalHarness Working Plan

> Status: Rolling construction plan
> Updated: 2026-09-07
> Governing target: `docs/SIGNALHARNESS_TARGET_STATE.md`

This document records where construction should go next and how each phase should be verified. It is intentionally mutable. After every completed phase, update Current State, Retrospective, Acceptance evidence, and the next phase before continuing.

## CURRENT STATE

Current repository baseline at planning time:

- branch: `main`
- HEAD: `682a299` — `Add project onboarding and live web change monitoring`
- tracked working tree was clean before these planning-document edits.
- existing product has project-scoped profile/watchlist state, GitHub release/issues, RSS, Web Snapshot/Diff, candidate funnel, deterministic scoring/guards, five-Agent runner, trace/evals, CLI/REST/SSE, Golden Demo, and five read-only MCP tools.
- current persistence is primarily YAML/JSON/file artifacts rather than a durable relational Change ledger.
- current workflow replaces normalized events with the candidate-funnel Top-K before assessment/output persistence.
- current GitHub signal collector is single-page `per_page=100` for releases/issues.
- current stream runs are in-process and start when the first SSE subscriber arrives.
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

### Explicitly defer

Do not redesign all Agents, add many new sources, or rebuild the frontend in P1.
## P2 — Scan Window & Durable Execution

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
## P4 — Analyzer/Harness V2 + Eval System

### Target

Determine which Agent/Harness components actually improve SignalHarness quality, instead of preserving the current five-Agent layout by habit.

### Recommended approach

Keep the current five-Agent runner as the baseline. Build a frozen evaluation corpus from deterministic edge cases plus a modest set of real Changes/Evidence. Compare harness variants on exactly the same inputs.

Suggested variants:

- current five-Agent runner;
- deterministic Supervisor/router + current downstream Agents;
- on-demand Evidence + separate Impact/Action;
- on-demand Evidence + merged ImpactActionAnalyzer;
- previous variant + SelectiveVerifier;
- optional episodic example retrieval when enough real examples exist.

### Measure

Relevance recall, false positives/negatives, impact/module correctness, citation support, action usefulness, Top usefulness, latency, tokens, and cost.

### Acceptance

- all compared variants use frozen Change/Profile/Evidence/Preference inputs;
- analyzer/model/prompt/context/policy versions are recorded;
- a simpler variant is preferred when quality is equivalent or better;
- no component is kept solely because it looks more agentic;
- the final real-time Agent count remains an evidence-based result, not a precondition.
## P5 — Product Intelligence Experience

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
- one core Scan application service used by Web/CLI/REST/MCP.

MCP must add a truthful fresh-scan tool plus status/result/list/detail tools. Long scans return a persistent handle rather than depending on one request staying open.

### Acceptance

- a real project query produces overall report → Top → All → detail as one coherent Scan;
- changing Top count does not change All count;
- report statistics/themes reflect the full relevant set, not only Top;
- pagination remains stable while new scans occur;
- three copy/export modes produce clean Markdown without default engineering Trace noise;
- MCP can trigger a fresh Scan when no prior result exists and later retrieve it;
- Web/CLI/REST/MCP projections agree on the same scan_id and product data.
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

**Next construction phase: P1 — Persistent Change Ledger.**

Before modifying implementation code, refresh Git/HEAD/status, run the project preflight/skill routing, inspect the minimum relevant persistence/workflow/tests, establish baseline behavior, and build one vertical slice that proves “deep-analysis Top-K does not delete All Relevant Changes”.

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