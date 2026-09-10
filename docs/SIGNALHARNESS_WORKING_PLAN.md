# SignalHarness Working Plan

> Status: Rolling construction plan
> Updated: 2026-09-09
> Governing target: `docs/SIGNALHARNESS_TARGET_STATE.md`

This document records where construction should go next and how each phase should be verified. It is intentionally mutable. After every completed phase, update Current State, Retrospective, Acceptance evidence, and the next phase before continuing.

## CURRENT STATE

Current repository baseline at planning time:

- branch: `main`
- planning baseline commit: `7e30625` — `Document SignalHarness target state and phased plan`.
- P1–P8 local implementation slices are complete through the current working tree; milestone Git/GitHub status is tracked by repository history and CI rather than transient text in this plan. P7 still has one environment-dependent external-destination delivery check, and P8 intentionally has no real policy promotion evidence until enough real user episodes exist.
- existing product has project-scoped Profile/Preference state, Local Git + GitHub release/issue/commit/merged-PR collection, PyPI, exact-version OSV, usage-bound RSS/Web sources, durable Change Ledger, persistent schedules, Inbox/Outbox delivery state, calibration episodes/replay, CLI/REST/SSE, Golden Demo, and the thin 10-tool MCP adapter. Golden Demo now also supports direct GitHub-URL onboarding, editable 1–3650 day scan windows, stable project-impact boards for All Relevant Changes, and a verified local OpenAI `gpt-5.6-sol` real-Agent path.
- project-scoped SQLite schema v6 now persists EventRevision/Change/ProjectImpact/Scan/ScanChange, Profile/Preference, window/source coverage, Schedule/Inbox/Outbox/DeliveryAttempt, durable calibration feedback, and ChangeOutcome state; legacy YAML/JSON outputs remain compatibility projections.
- normalized/deduplicated candidates persist before Top-K; All Changes remain queryable independently of deep-analysis budget.
- GitHub release/issue collection now follows pagination with a bounded cap and explicit `partial/history_limited` coverage when the cap is reached.
- stream runs start on POST, persist queued/running input/status for bounded restart recovery, and use SSE only as an in-process observation/replay surface.
- current onboarding is auto-active from bounded manifest/lockfile/local-Git evidence; explicit user preferences remain separate and higher authority than refresh/inference.
- current real-agent scan already defers LearningPolicyAgent reflection from the critical path.

The existing five-Agent implementation is a baseline to preserve for comparison, not the future product contract.
## P0 RESET — Product Surface + Full-Set Environment Synthesis + Architecture Convergence

**Status: CURRENT HIGHEST PRIORITY — owner review on 2026-09-11 supersedes the previous “move directly to pilot” next action.**

P1–P8 remain useful implementation history and tested infrastructure, but they do not mean the current product surface or semantic data flow is accepted. Before a broader pilot, converge the system around the actual product experience.

### P0-A — Full-set environment synthesis

- Keep deep semantic impact/action analysis bounded to roughly 10–15 high-value Changes.
- Explicitly separate **Observed Change Corpus → Relevant Projection → Deep-analysis Shortlist**. The current code incorrectly equates pre-funnel deduplicated count with `relevant_count`; fix that semantic boundary.
- Build an `EnvironmentSynthesis` path that receives the full frozen **Observed/aggregated Change corpus** for the period (for example 300 Changes) through bounded `ChangeDigest` contracts, so weak individual signals can still combine into an emerging direction. Project relevance is an input/weight, not an early Top-K visibility cutoff.
- The user-facing “All Relevant Changes” projection is a separate, recall-oriented project-relevance view and must not contain raw source noise merely because it was observed.
- The model-written environment report and “directions to watch” must be grounded across the full environment corpus and reference supporting Change IDs/evidence; project-specific claims must additionally be grounded in Project Profile/usage evidence.
- Top-K is an analysis budget only. It must never become the semantic horizon for the period report.
- Prefer Change/ChangeRevision as the synthesis/analyzer unit; do not make source-level `SignalEvent` the permanent product reasoning unit.

### P0-B — Frontend product redesign

Frontend quality is a product blocker, not optional polish. The normal Web surface should lead with **period environment brief → directions/themes → items requiring attention → all relevant changes → detail**.

Remove from the normal product UI:

- numeric impact/relevance scores;
- `mock-agent`, deterministic/demo, fixture and Harness selectors;
- “deep analyzed” as a primary metric;
- raw SSE / Trace / LLM-call counters and permission/debug vocabulary;
- engineering-only provider/runtime concepts unless the user explicitly opens Settings/Audit.

Keep internal score, modes and Trace for ranking, tests, CLI/dev tools and Audit. Production Web runs use the configured real analysis policy automatically.

### P0-C — Product-level live execution

Raw `trace.step` remains an audit stream, but the primary live experience needs a stable product event contract such as:

```text
collecting_sources
→ normalizing_and_aggregating
→ synthesizing_environment
→ deep_analyzing_priority_changes
→ assembling_report
→ complete
```

Expose meaningful progress/count deltas and newly formed themes/changes without rendering every low-level Python/LLM Trace row as the main scan experience.

### P0-D — Architecture conflict cleanup

- Introduce first-class ChangeRevision / aggregated evidence before semantic analysis rather than aggregating only after Event-oriented processing.
- Introduce a first-class versioned Report/EnvironmentSynthesis projection. Do not copy one global `report_summary_zh` into every per-Change Assessment as the durable representation.
- Make the relational ledger the durable product source of truth; demote legacy signal/feedback JSON and YAML-derived runtime memory to bootstrap/compatibility/export roles.
- Remove hard-coded `12` policy leakage from frontend/API contracts; analysis budget belongs to backend policy/configuration.
- Move All Changes filtering/sorting/pagination into the persistence query rather than loading an entire Scan into Python before each page.
- Normalize product feedback APIs around `change_id + scan_id + event/change revision`, not legacy `signal_id` naming.
- Reduce legacy UI/output fallback paths once the Product Intelligence contract is proven.

### P0-E — Model profiles

- Default Kimi profile: `kimi-k3` (official flagship, verified 2026-09-11).
- Default DeepSeek profile: `deepseek-v4-pro` (official GA API model, verified 2026-09-11).
- Keep provider-native tools disabled in SignalHarness until the runtime explicitly implements that authority path; model capability does not silently change Tool/permission ownership.

### P0 acceptance

- a controlled Scan with >=300 observed/aggregated Changes produces a model-written full-period environment brief and directions grounded across the full corpus while only a bounded shortlist receives expensive deep analysis; Observed, Relevant and Deep-analyzed counts remain semantically distinct;
- report claims are traceable to supporting Change IDs and do not silently over-generalize from Top-K;
- ordinary Web UI contains no numeric impact score, mock/demo/fixture mode, Harness implementation selector, or raw Trace console in the primary flow;
- live scan progress is understandable without knowing SSE, Agents, schemas or Trace internals;
- ChangeRevision / aggregation boundary and durable state authority are explicit and tested;
- current Kimi and DeepSeek profiles resolve to `kimi-k3` and `deepseek-v4-pro`;
- full Python/frontend verification remains green after convergence.

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
→ ChatGPT Web + Mac Developer Bridge Acceptance
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
- Historical first 40-case offline run: five-Agent 6 LLM calls / 1.000 decision; deterministic Supervisor 5 / 1.000; deferred Learning 4 / 1.000; deterministic Evidence 2 / 0.975; selective EvidenceResearcher 3 / 1.000. That 0.975 result was evidence at the time, not a permanent contract that the simpler variant must remain worse.
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
- At the P6 slice, release-lineage repair showed that the earlier deterministic-Evidence loss came from shared source-lineage behavior rather than missing EvidenceResearcher reasoning. At that point `deterministic-evidence-resolver` became the cheapest offline candidate, while the five-Agent real-provider default remained protected pending live-model evidence. This historical gate is superseded by the Eval V2 + Qwen production-style smoke evidence below.
- Generic Monitor live comparison remains environment/owner-controlled because it invokes a paid external model. Its harness is implemented and ready, but no external model cost was incurred automatically.

### P4 Eval V2 foundation — 2026-09-10

The earlier P4 ablation results remain useful historical **Regression** evidence, but they are no longer treated as sufficient proof that the cheapest same-score variant has equal product intelligence. The 40-case `resume-v1` suite protects solved decision/category behavior; it does not measure grounded technical explanation, project-specific why, uncertainty, action quality, ranking order, or trajectory quality.

- Added `signalharness-capability-v1`: 32 curated hard cases with 10 grade-3 priorities, 10 hard negatives, 9 explicit uncertainty cases, and broad dependency/MCP/provider/security/checkpoint/eval/competitor/project-code/collection coverage. Labels are semantic rubrics, not reference-answer text.
- Added `signal-harness capability-eval`: `shared-evidence-single-agent` (one semantic call) and `split-impact-action-narrative` (three semantic calls) receive the exact same Event, Project Profile, deterministic Route, and curated Evidence and use the same Python guarded scoring/decision. Metrics include decision/category, nDCG@5/10, fact grounding, project specificity, action coverage, uncertainty, forbidden claims, internal leakage, language, hard negatives, consistency, calls/tokens/cost/fallback.
- Mock Capability runs are explicitly `plumbing_only` and cannot recommend production architecture. The first mock run is intentionally non-perfect: current gaps are exposed instead of returning another 1.0 self-confirming benchmark. Real runs with <3 trials are insufficient; >=3 trials produce a review matrix but still do not auto-promote an architecture.
- Added 8 per-case Trajectory contracts and `signal-harness trajectory-eval`; current local contract acceptance is 8/8.
- Added a 16-case Narrative calibration seed and blind A/B export. Review files omit variant identity; mapping is separate. Mock pairs are never production-calibration eligible. Even 15+ valid human labels only make a future judge-calibration experiment ready; `judge_calibrated=false / judge_enabled=false` remains until agreement/bias checks are implemented and pass.
- Negative/ambiguous feedback now freezes exact Event/Assessment/ScanChange revision into project-scoped `golden_candidates.json`. `golden-review-draft` requires explicit human truth/relevance/rubric annotation; there is no automatic Candidate → Golden promotion.
- No LangSmith/Braintrust/Phoenix dependency was added: the current bottleneck is evaluation data/grader quality, not an Eval dashboard.
- First repeated real-provider evidence is now available from Qwen `qwen-plus`: 32 Capability cases × 3 trials at batch=4. All six single/split trial checkpoints are coverage-complete, schema-valid and zero-fallback. Before deterministic rescoring, both variants had decision acceptance 0.7812; split improved fact coverage (0.8247 vs 0.7986) and project specificity (0.7284 vs 0.5370) while using 72 vs 24 calls and 291,485 vs 102,627 tokens. This supports a real quality/cost tradeoff but does not authorize a production Harness switch.
- GPT-5.6 Sol did not produce valid Capability evidence in this slice because every configured OpenAI API request returned 429 `credit_balance_exhausted`; those fallback-derived metrics are explicitly invalid. DeepSeek passed a 0.94s health probe but heavy structured Capability batches produced unacceptable 75s/ReadTimeout long tails, so no valid DeepSeek 3-trial matrix is claimed.
- Long real-provider Eval now checkpoints each completed variant/trial, prints per-batch progress, and reuses only coverage-complete/schema-valid/zero-fallback checkpoints under a matching experiment signature. Current prompt contract is `signal-harness-llm-v3`; schema-valid but incomplete Narrative results receive one targeted missing-ID repair before deterministic fallback.
- Added `guarded-scoring-v2` and `signal-harness capability-regrade`. Frozen real semantic checkpoints can be re-scored with zero provider calls, so Python scoring/policy changes no longer force repeated model generation. Evidence-aware floors suppress escalation when evidence is uncertain, use strong-semantic or semantic+deterministic corroboration for verified direct-impact alerts, and use only a SAVE floor for high-relevance engineering opinion. User Ignore/already-satisfied/noise overrides remain final.
- The Qwen checkpoint regrade under `guarded-scoring-v2` reaches decision acceptance 1.000 for both variants, nDCG@5 1.000 and nDCG@10 0.9202, while preserving the original Narrative quality differences. The frozen 40-case Regression returns to decision/precision/recall 1.000/1.000/1.000.
- Harness ablation is now genuinely offline/frozen: external source tools are fixture-safe mocked only inside `run_harness_ablation`, while local fixture reads remain real. With an intentionally invalid GitHub token, both the 40-case and 15-case real-world corpora give all current Harness variants deterministic offline results, eliminating credential/network leakage into architecture comparison.
- Added `/eval/narrative` as a dedicated local blind human-review surface over the canonical Qwen pairs. The reviewer API omits variant identity, decision, and impact score; A/B mapping remains separate. Overall choice plus all seven rubric dimensions are required before atomic persistence.
- First real human quick review on the **pre-presentation-v2** outputs recorded overall choices `B/B/B/B/A` for the first five blind pairs. Revealing only the stored mapping afterwards shows all five selected `split-impact-action-narrative`. Qualitative feedback identified weaker prose and leaked `Approval required before / is not enabled / Human approval` runtime text on the single-Agent side. This is five-case historical evidence, not an architecture promotion gate; it is archived in `outputs/narrative-calibration-pre-presentation-v2/`.
- Added `presentation-v2` as a deterministic user-copy boundary. Permission/audit truth remains in raw `action_items` and Trace, while user-facing `action_items_zh`, Product Intelligence, Capability observed outputs, and reviewer actions are sanitized. Substantive Chinese steps can be extracted from permission wrappers; internal ids/English permission boilerplate/short runtime labels are removed.
- Upgraded prompts to `signal-harness-llm-v3`, but a paid Qwen five-case diagnostic proved prompt instructions alone still generated raw approval boilerplate. The deterministic sanitizer is therefore required. Replaying the same diagnostic through presentation-v2 yields internal-leakage pass rate 1.0 for both single and split. Automated first-five metrics are mixed (single project/action coverage can exceed split while split fact coverage is higher), reinforcing that human overall prose preference is not reducible to one scalar grader.
- Upgraded Capability grader to `capability-grader-v4`: runtime permission strings are explicit internal-leakage failures, while debug-phrase matching no longer falsely flags normal prose such as `迁移影响分析` as the field `影响分`.
- Because the evaluated presentation changed, the canonical post-fix 16-pair review was reset to 0/16 instead of inheriting old labels. Browser acceptance confirms the post-fix first pair is clean.
- Adaptive-routing shadow over the frozen 3×32 Qwen outputs rejected semantic pre-escalation: uncertainty-only selected 9/32 and uncertainty+risk selected 16/32, with no decision/nDCG improvement while estimated per-trial calls rose from 8 to about 15–16. Therefore “hard-looking input” is not yet a justified multi-Agent trigger.
- Added `deterministic-evidence-impact-action`: deterministic Supervisor + deterministic Evidence Resolver + one merged ImpactAction call + one Narrative call. Both the 40-case and 15-case real-world Harness corpora remain decision/precision/recall 1.000/1.000/1.000, with **2 LLM calls** versus 3 for `deterministic-evidence-resolver`; offline recommendation now moves to the 2-call variant.
- Added contract-driven adaptive fallback for that candidate. `schema_validation` or `coverage_validation` on the merged call escalates to the existing split Impact → Action path; `provider_timeout` / `provider_error` do not trigger extra same-provider calls. Trace now exposes `metadata.failure_kind` for those four failure classes. Focused tests cover invalid JSON, missing-event coverage, and provider outage behavior.
- A paid Qwen `qwen-plus` production-style Analyzer smoke was explicitly authorized and passed on four representative frozen real-world cases (`rw-001`, `rw-003`, `rw-004`, `rw-014`): decision/category match 4/4 + 4/4, exactly two schema-valid calls, zero fallback, zero adaptive split escalation, 10,597 provider-reported tokens, and 47.3s summed LLM latency. The local Qwen profile has no authoritative price metadata, so the trace `$0.0` value is not interpreted as actual zero billing cost. The smoke validates real-provider Analyzer execution, not fresh live-source collection. Evidence: `outputs/adaptive-qwen-real-smoke/smoke_summary.{json,md}`.
- After that smoke, `RunMode.AGENT` now defaults to `deterministic-evidence-impact-action`; `RunMode.MOCK_AGENT` keeps five-Agent as the comprehensive offline baseline, and explicit `--harness-variant five-agent` remains the rollback/comparison path.
- The smoke exposed one remaining presentation leak in legacy outputs. Radar Digest and Alerts Markdown now render Chinese Narrative fields instead of raw reason/action audit strings; Dashboard recommendations and Demo legacy fallback also use presentation fields. Raw permission/fallback/score material remains available in machine JSON/Trace.
- CLI real-agent credential loading now matches `serve`: `scan --mode agent` loads project `.env` without overriding explicit environment variables, while demo/mock never load real credentials. Tests explicitly prevent source-checkout `.env` from leaking into offline/missing-key cases.
- **Evidence intentionally open:** post-fix Narrative labels are still 0/16, so `judge_calibrated=false / judge_enabled=false`; the five archived pre-fix choices remain diagnostic historical evidence only. GPT-5.6 Sol must be rerun after API credits become available.

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
- Golden Demo now consumes the Product Intelligence contract after a run and presents Profile → Overall Report → Top → All → Detail → Agent Audit. Guarded analysis is followed by one presentation-only `ProjectNarrativeAgent` call that writes the human Chinese report / what / why / actions without changing score or decision; priority cards render those actions directly. Legacy `signals` / `assessments` remain a compatibility fallback/audit source, not the primary product view.
- Fresh local acceptance: 274 tests PASS; `git diff --check` PASS; JavaScript syntax PASS; Ruff PASS; strict mypy PASS (104 source files); CLI mock-agent JSON/read/export + trace/calibrate smoke PASS; REST/Web product smoke PASS; `uv build` PASS.
- Thin MCP adapter is complete: 10 tools total, with 9 read-only retrieval/context tools plus non-idempotent `signalharness_start_scan`. MCP fresh scans reuse the same persistent `StreamRunManager`; status/product/list/detail delegate to the same product/run state rather than duplicating business logic.
- MCP acceptance covers persistent fixture scan start → status → product/list/detail, rejected out-of-scope fixture paths, truthful tool annotations, and completed-run reads after rebuilding the MCP server object. FastAPI and MCP share one manager in service mode.
- Final fresh P5 local acceptance: 277 tests PASS; Ruff PASS; strict mypy PASS (104 source files); JavaScript syntax PASS; `uv build` PASS; regression-eval decision/precision/recall 1.000/1.000/1.000; project-eval 3/3 PASS. A full visual/human browser polish pass remains optional owner acceptance, not a blocker for objective P5 completion.
- 2026-09-10 frontend/Trace craft pass used three reusable global Skills installed outside the repo (`frontend-design`, `web-design-guidelines`, `frontend-design-review`) to review hierarchy, interaction friction, accessibility, and AI transparency without migrating the native HTML/CSS/JS stack. The project profile now uses progressive disclosure for the large importance-rule grid; form labels/focus/skip-link/`aria-live` coverage were tightened; long Trace/change regions use bounded scrolling/content visibility and reduced-motion handling.
- Golden Demo now shows the **actual SSE TraceRecorder stream** beside the Scan control. `trace.step` exposes the running Trace before a provider call and `trace.step.updated` updates the same index. LLM rows are native expandable `<details>` cards with structured model-output summaries plus schema/tool/permission/fallback/latency/token audit; the full Audit view reuses the same data. The pipeline is derived from actual Trace stages instead of a fixed five-Agent diagram.
- Added `structured-reasoning-v1` in `agent_integration/reasoning_summary.py`. It derives only bounded public summaries from validated Agent output fields and explicitly marks `reasoning_disclosure=structured_model_output_summary_not_hidden_chain_of_thought`; raw provider responses and prompts are not exposed. Running steps use `waiting_for_model`, validated outputs use `available`, and fallback-derived summaries are labeled `fallback_output`.
- Browser acceptance on `http://127.0.0.1:8001/demo` with fixture + mock-agent observed 35 SSE events / 22 real Trace rows / 7 LLM-path calls, expanded an Impact trace to inspect per-event relevance/risk/modules and technical audit, collapsed it again, verified expand-all/collapse-all, language-state re-render, and selected the dynamic Analysis pipeline stage. No real-provider cost was incurred for this UI acceptance.
- The subsequent owner visual review rejected the native HTML/CSS layout as insufficiently polished, so the Golden Demo was deliberately migrated instead of receiving another CSS-only pass. `frontend/` is now React + TypeScript + Tailwind source with Vite output written to the existing package static filenames. FastAPI routes and backend contracts remain unchanged; Narrative review assets are preserved by the build. The new information architecture is Project Environment Intelligence rather than a generic SaaS card grid: compact project-status strip, immediate Runtime + dark real-Trace workbench, progressive Project Context, collapsed Monitoring, report/priority/all-change reading flow, right-side Change Drawer, and low-priority full Audit. A second visual pass explicitly removed the initial landing-page hero and replaced source/mode/window dropdowns with accessible segmented controls so the core scan is visible and operable immediately.
- React browser acceptance repeated the fixture + `mock-agent` workflow through `#dataSource` / `#runBtn`: `run-a614e68a75e2` completed with 35 SSE / 22 Trace / 7 LLM-path calls, updated 4 complete / 4 analyzed / 1 alert Product Intelligence state, expanded the Impact Analyst structured reasoning, and opened the real Change Detail API drawer with evidence/feedback/outcome controls. No feedback/outcome was written and no real-provider cost was incurred.
- Second-pass workbench browser acceptance used the final segmented source control and completed `run-67bdbacd705b` with the same 35 SSE / 22 Trace / 7 LLM-path execution and 4 complete / 4 analyzed / 1 alert Product state. This verifies the denser control surface without changing the backend workflow or using a real provider.
- Second-pass workbench final gate: **384 tests PASS in 86.40s**; frontend clean install / strict TypeScript / Vite build PASS; npm audit 0 vulnerabilities; Ruff PASS; strict mypy PASS (120 Python source files); wheel contains compiled Demo + Narrative review assets; sdist contains the React/Tailwind source tree; `git diff --check` PASS.
- React/Tailwind migration final gate: `npm ci` reports 0 vulnerabilities; TypeScript strict typecheck PASS; Vite production build PASS at ~95.4 KB gzip JS + 8.6 KB gzip CSS; **384 pytest tests PASS in 38.68s**; Ruff PASS; strict mypy PASS (120 Python source files); Narrative JS syntax PASS; wheel contains both compiled Demo and Narrative review HTML/CSS/JS; `git diff --check` PASS. No-cost evidence gates remain Regression 1.000/1.000/1.000, Project Context 3/3, Trajectory 8/8, both Harness corpora 1.000 with the 2-call recommendation, and frozen Qwen regrade decision=1.000 / nDCG@5=1.000.
## P6 — Source Intelligence Expansion

**Status: IMPLEMENTED / AGENT ACCEPTANCE PASS**

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

### P6 Slice 1 implementation evidence — PyPI registry + release identity

- Added the official read-only PyPI JSON Simple/Index connector with bounded retries, API-version validation, PEP 440 release grouping, yanked metadata, ETag/last-serial diagnostics, explicit history caps, and truthful `complete/partial` coverage.
- Project onboarding/watchlists now attach source-owned `package_name + package_registry` identity to known dependency GitHub releases and can monitor configured PyPI packages directly.
- Change identity for releases is now `(registry, canonical package, normalized version)` when the source actually owns that package fact. GitHub + PyPI observations for the same package/version aggregate into one Change while retaining separate EventRevisions/evidence; unrelated packages do not merge.
- Project-state applicability uses lockfile/profile resolved versions plus source identity instead of body mentions. Release lineage now preserves source-provided `previous_version`; this also removed the historical deterministic-Evidence ablation loss without adding an Agent call.
- Connector failure semantics are regression-covered: permanent HTTP failure is explicit; a failed Registry source plus another successful source yields a partial Scan and does not advance the interactive checkpoint.
- Real-source smoke on 2026-09-09: official PyPI `pydantic` returned coverage=complete, 205 releases, latest `2.13.5`, previous `2.13.4`. No credentials or paid model calls were used.
- Fresh local gate for this slice: 291 tests PASS; Ruff PASS; strict mypy PASS (106 source files); regression-eval PASS at decision/precision/recall 1.000/1.000/1.000; project-eval 3/3 PASS; harness-eval PASS with `deterministic-evidence-resolver` recommended on frozen offline inputs; `uv build` PASS.
### P6 Slice 2 implementation evidence — own-project Local Git + OSV exact-version security

- Added a read-only Local Git connector that records source-owned repository identity, full commit SHA, author/commit timestamps, parents, merge status, recognizable PR number, and GitHub commit URL when the origin is GitHub. Collection is bounded; reaching the commit cap reports `partial/history_limited` rather than false completeness.
- Local Git and GitHub commit/merged-PR normalization now share deterministic `(repository, commit SHA)` Change identity. Focused tests prove equivalent Local Git + GitHub observations aggregate to one Change while retaining separate observations.
- Added a read-only OSV connector that queries only exact resolved `name + ecosystem + version` facts derived from allowlisted lockfile/profile evidence. It supports bounded pagination/concurrency, partial per-dependency failure diagnostics, and source failure when every dependency query fails.
- Security advisory identity prefers CVE alias, then GHSA/OSV id. Project applicability matches the advisory back to the actual direct dependency; vulnerable installed versions do not reuse release semantics where `installed` means already satisfied.
- Project onboarding now records dependency ecosystem, enables OSV when exact supported resolved versions exist, and CLI/local project connection adds the actual project repository as a Local Git source. The default SignalHarness profile/watchlist now exercises both Local Git and OSV.
- Source permissions/policy, source authority/noise allowlists, product labels, and project watchlist metadata are wired for both connectors; untrusted source content gains no new tool/permission authority.
- Deterministic acceptance includes Local Git identity/history-cap tests, exact OSV request/version tests, advisory identity/project-applicability tests, cross-source Git aggregation, and a full Scan that persists both `local_git_commit` and `security_advisory` into the frozen Change Ledger.
- Real-source smoke on 2026-09-09: current repository resolved as `pocketvin/signalharness`; live OSV queries for `pydantic==2.13.4` and `httpx==0.28.1` both succeeded with coverage=complete and returned 0 advisories in that smoke. No credentials or paid model calls were used.
- Fresh local gate after Slice 2: 296 tests PASS; Ruff PASS; strict mypy PASS (108 source files); mock-agent Scan/Trace/Calibration PASS; regression-eval decision/precision/recall 1.000/1.000/1.000; project-eval 3/3 PASS; harness-eval PASS with `deterministic-evidence-resolver` still recommended on frozen inputs; `uv build` PASS; `git diff --check` PASS.
### P6 Slice 3 implementation evidence — GitHub commits + merged PRs

- GitHub collection now supports `fetch_repo_commits` and `fetch_repo_merged_pulls` in addition to releases/issues. Commit requests use GitHub's native `since`; merged-PR requests use closed PRs sorted by `updated desc`, stop safely once page `updated_at` is older than the window, then require `merged_at` inside the requested window.
- Commit observations retain full SHA, parent SHAs, GitHub author/committer logins, signature-verification metadata, repository identity, and official provenance. Merged PR observations retain merge commit SHA, author/merger, base/head refs, labels, timestamps, and PR URL.
- Local project onboarding detects a GitHub `origin` without executing project code and adds the own repository as `project_owned` with commit + merged-PR monitoring. Own-project Git facts receive an explicit relevance signal rather than masquerading as an upstream release.
- Local Git commit, GitHub commit, and merged PR observations that resolve to the same normalized repository + final commit SHA aggregate into one Change while preserving distinct evidence/EventRevisions. Agent evidence-tool contracts, permission routing, mock-provider behavior, source authority, classification, and scoring all understand the new GitHub source types.
- Real-source smoke on 2026-09-09 used the already-authorized local GitHub CLI credential without printing the token: `pocketvin/signalharness` returned 19 commits from the prior 7 days with coverage=complete / 1 page; the merged-PR endpoint returned 0 current matches with coverage=complete / 1 page. Both reported GitHub API version `2026-03-10`.

### P6 Slice 4 implementation evidence — usage-bound official changelogs

- RSS/Web source jobs can now carry source-owned `entity_type + entity_name` for dependency/provider/protocol/runtime. The binding survives collection and normalization into the Event raw contract.
- Project applicability uses exact structured Profile membership for a bound entity. A provider/spec/runtime page does not become relevant merely because its body or source name happens to mention a familiar keyword. Conversely, a bound source can match even when a short diff excerpt does not repeat the entity name.
- Structured Critical/Important/Normal/Low/Ignore preferences now match these resolved entity identities directly for dependency/provider/protocol/runtime scopes instead of relying only on free-text overlap.
- Onboarding binds the official OpenAI API changelog to `provider: OpenAI API` only when the `openai` dependency establishes provider usage, and binds the MCP specification to `protocol: Model Context Protocol` when MCP usage is detected. The default SignalHarness MCP source carries the same protocol binding.
- Construction evidence showed the all-history FastAPI release-notes page exceeds the bounded 1 MB Web Snapshot contract. SignalHarness therefore keeps FastAPI coverage on the already-structured GitHub + PyPI sources rather than globally relaxing the Web content bound for one oversized page.
- Real-source Web Snapshot smoke on 2026-09-09 successfully created clean baselines for the official OpenAI API changelog and MCP specification; MCP's canonical final URL resolved to the dated `2026-07-28` specification. No paid model call was used.
- Final P6 local gate: 305 tests PASS; Ruff PASS; strict mypy PASS (108 source files); regression-eval decision/precision/recall 1.000/1.000/1.000; project-eval 3/3 PASS; harness-eval PASS with `deterministic-evidence-resolver` still recommended; mock-agent Scan/Trace/Calibration PASS; `uv build` PASS; `git diff --check` PASS.
- **P6 acceptance is complete locally.** Remaining source additions are incremental breadth, not blockers for the phase contract.

## P7 — Continuous Monitoring

**Status: IMPLEMENTED / LOCAL ACCEPTANCE PASS — one real external destination delivery remains environment-dependent**

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

### P7 implementation evidence — Schedule / Inbox / Outbox / signed Webhook

- SQLite schema v5 adds persistent `schedules`, `inbox_items`, `notification_outbox`, and `delivery_attempts`. Schedule checkpoint, manual interactive checkpoint, Inbox read state, and delivery state are separate durable records.
- `ScheduleManager` is a thin trigger layer over the existing `StreamRunManager → SignalHarnessWorkflow`; it does not own a second collection/analysis implementation. Scheduled runs are created with `interactive=false`, a schedule-scoped consumer id, and a frozen custom `[L,U)` window.
- Supported first cadences are 12h, 24h, and daily local-time schedules with IANA timezone handling. Missed timer ticks coalesce into one catch-up Scan from the last successful schedule checkpoint rather than replaying a backlog of stale ticks.
- Schedule checkpoint advances only after successful non-partial coverage. Partial/error runs retain the old checkpoint, so a future run re-covers the unresolved interval. Manual `since_last` remains untouched.
- Service startup first recovers queued/running StreamRuns, then reconciles schedules. Tests cover the crash boundary where a Scan already completed and wrote `service_run.json` but the process died before schedule checkpoint/Inbox finalization; restart completes those durable side effects without moving the manual checkpoint.
- FastAPI now exposes project-scoped schedule create/list/disable plus Inbox list/unread/read endpoints. Schedule mutation stays behind the explicit `manage_schedules` permission class.
- Golden Demo now exposes a project-scoped Continuous Monitoring surface directly after Project Profile: users can configure 12h/24h/daily-local-time schedules, choose analysis mode, see next-run/checkpoint/status, disable schedules, inspect Inbox/delivery readiness, refresh, and mark Inbox items read without hand-writing REST calls.
- Real Chrome acceptance verified both empty-state rendering and a persisted `daily 09:30 Asia/Tokyo / demo` schedule rendered from the isolated backend as `1 / 1` with next-run/status controls. MDB background synthetic-click automation later became stale/timeout-prone and could not be treated as reliable click evidence; create/list/disable/read actions are therefore acceptance-covered by FastAPI/TestClient lifecycle tests rather than falsely claiming the flaky automation as product behavior. No real project schedule state was mutated during browser acceptance.
- High-priority notifications are projected from frozen `ScanChange + EventRevision + Assessment`, not from legacy `alerts.json`. The logical notification key includes Change + EventRevision + decision, so identical retries deduplicate while a new revision or an `alert → action_required` escalation can intentionally create a new Inbox item.
- Optional signed Webhook delivery uses runtime-only `SIGNALHARNESS_WEBHOOK_URL` + `SIGNALHARNESS_WEBHOOK_SECRET`; neither value is stored in SQLite, Watchlists, normal output files, or trace. HMAC-SHA256 signature, stable idempotency key, delivery id, retry backoff, terminal failure, and every DeliveryAttempt are persisted/auditable.
- Local `alerts.json` / `alerts.md` remain compatibility artifacts and are explicitly not counted as external delivery proof.
- Real network-stack acceptance uses an actual loopback HTTP receiver (not httpx MockTransport): a signed notification reached the receiver, preserved its idempotency key, and linked to the durable Scan report/product/change paths. Mock-transport tests separately cover HTTP 503 → retry → 204 success with one Outbox identity and two recorded DeliveryAttempts.
- Fresh P7 local gate: 315 tests PASS; Ruff PASS; strict mypy PASS (111 source files); regression-eval decision/precision/recall 1.000/1.000/1.000; project-eval 3/3 PASS; harness-eval PASS with `deterministic-evidence-resolver`; mock-agent Scan/Trace/Calibration PASS; `uv build` PASS; `git diff --check` PASS. Additional focused restart/loopback tests added after that gate also PASS.
- **Environment-dependent acceptance still open:** no user-owned public/external webhook destination has been provided, so SignalHarness has not sent a notification to a real external user destination. The implementation is ready; do not claim that last acceptance until an authorized destination is configured and receives one message.

## P8 — Calibration Engine

**Status: IMPLEMENTED / LOCAL ACCEPTANCE PASS — real-user episode volume and real promotion evidence pending**

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

### P8 implementation evidence — durable feedback/outcomes, frozen Episodes, shadow replay, gated promotion

- SQLite schema v6 adds project-scoped `calibration_feedback` and `change_outcomes`. Existing `/feedback` and CLI feedback keep legacy JSON compatibility but now also attach real feedback to the exact frozen `ScanChange / EventRevision` whenever that fact exists.
- FastAPI adds project-scoped Outcome create/list and Calibration read endpoints. Outcome records require at least one observed fact (`impact_observed`, `action_taken`, `action_helpful`, or `resolved`) and pin the exact frozen Change revision instead of writing an unscoped note.
- Golden Demo Change Detail now provides the real data-entry path: useful / not useful / false positive / too generic feedback plus optional observed-impact/action/helpfulness/resolution outcomes. Recording these facts does not automatically mutate ranking policy.
- `CalibrationDataset` v1 deterministically projects durable feedback/outcomes onto frozen Episodes. Positive/negative evidence becomes a hard replay label; contradictory real evidence is preserved as `ambiguous` and excluded from hard metrics instead of being arbitrarily resolved. Feedback that cannot be attached to a frozen Change is counted explicitly as orphan feedback.
- Candidate policies replay on the frozen EventRevision with the current effective Project Profile. The default promotion evidence floor is 3 labeled Episodes. Below that floor the result is `insufficient_evidence`; a candidate with no measured gain is `reject_no_gain`; any guarded precision/recall/false-positive/missed-positive regression is `reject_regression`. Only a measured non-regressing improvement becomes `review_and_consider / promotion_allowed=true`.
- Shadow evaluation is first-class and per Episode: old/proposed score, rank, decision, and—when a frozen Assessment exists—notification eligibility are recorded before activation. Focused acceptance proves rank swaps, `save → alert` decision changes, and notification `false → true` changes are observable rather than hidden behind aggregate precision.
- Real project policy apply cannot bypass this durable replay. When a real `change_ledger.sqlite3` exists, `apply_staged_learning` requires `calibration_replay.json` with `promotion_allowed=true` in addition to the existing risk/replay/human-approval gates. Legacy/manual staging without a Ledger remains compatibility-only and does not fabricate historical Episodes.
- Every applied policy writes a versioned `policy_revisions/policy-revision-*.json` snapshot with old/new policy and proposal id. `learning-rollback --revision-id ... --yes` restores the old revision only when the active policy still matches that revision's new policy, preventing rollback from overwriting a newer change; repeated rollback is rejected and recorded.
- The existing Scan hot path remains independent from Calibration. A Scan can run normally with zero calibration records, and LearningPolicyAgent cannot directly promote its own policy proposal.
- Real current-project smoke on 2026-09-09 is intentionally conservative: legacy JSON feedback can still generate a review candidate, but the durable dataset currently has `labeled=0`; `signal-harness calibrate --mode demo` therefore reports `insufficient_evidence / promotion_allowed=false`. This is the desired proof that SignalHarness does not manufacture “learning success” before real user outcomes exist.
- Final fresh local code gate after the React/Tailwind Golden Demo migration: **384 tests PASS in 38.68s**; frontend TypeScript/Vite build PASS; npm audit reports 0 vulnerabilities; Ruff PASS; strict mypy PASS (**120 source files**); Narrative JavaScript check PASS; `uv build` includes the compiled Demo + Narrative review assets; `git diff --check` PASS. Follow-up no-cost evidence gates keep Regression decision/precision/recall at 1.000/1.000/1.000, Project Context 3/3, Trajectory 8/8, both 40-case and 15-case Harness ablations PASS with `deterministic-evidence-impact-action` recommended at 2 calls, and frozen Qwen regrade decision=1.000 / nDCG@5=1.000 / nDCG@10=0.9202 for both fair-baseline variants.
- **Environment/pilot evidence still open by design:** no real policy candidate has been promoted because the current project has not yet accumulated the minimum real labeled Episodes. P8 implementation is locally complete, but real improvement/promotion should only be claimed after actual user feedback/outcomes produce a replay improvement.

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
- real `agent` scans default to `deterministic-evidence-impact-action`; `mock-agent` retains five-Agent baseline coverage and explicit five-Agent rollback remains supported.
- adaptive multi-Agent escalation is contract-driven (schema/coverage failure), not triggered merely because an event looks high-risk or uncertain.
- human-readable product outputs consume presentation fields; raw reason/action/score/debug data remains audit-only.
- normal acceptance is performed by ChatGPT Web + Mac Developer Bridge, not nested completion reviewers.

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

**Complete the P0 reset before treating the current UI/data flow as pilot-ready.**

P1–P8 are locally implemented and remain valuable infrastructure, but the 2026-09-11 owner review found four product blockers: the model-written environment narrative currently sees only the deep-analysis shortlist rather than the full relevant set; the normal Web surface exposes engineering/test concepts and numeric scores; raw Trace/SSE is being used as the main live experience; and Event-oriented/legacy-state boundaries still conflict with the target Change-centric architecture. Resolve P0-A through P0-E first. Then move to real-usage pilot, collect feedback/outcomes, and use the existing Calibration replay/promotion gates rather than adding another framework layer. P7's final external-destination delivery remains separately environment-dependent.

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