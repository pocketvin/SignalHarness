# SignalHarness Target State

> Status: Confirmed strategic baseline
> Updated: 2026-09-11
> Scope: Product target, durable architecture principles, and decision boundaries

This document records the latest confirmed direction for SignalHarness. It is intentionally more stable than implementation plans. When older architecture documents describe the current five-Agent implementation, they remain valid as **current-state facts**; when they conflict with the future product direction below, this document governs the target state.

## 1. Product definition

SignalHarness is a **Project Environment Intelligence** system for continuously developed software projects.

After a user connects a GitHub repository or authorized local project, SignalHarness should automatically build and continuously maintain a project environment profile, collect environmental changes from relevant sources, decide which changes matter to that project, explain the impact, recommend actions, and expose the same intelligence through Web, CLI, REST API, and MCP/Tool interfaces.

The product must continuously answer four questions:

1. What changed around my project?
2. Which changes actually matter to this project?
3. What could they affect?
4. What should I do?

Multi-Agent orchestration, single-Agent analysis, rules, search, scoring, and model choice are implementation techniques rather than the product identity.
## 2. Core product experience

A normal project flow should be:

```text
Connect project
→ auto-generate and activate Project Profile
→ collect environment changes
→ normalize / revision-track / deduplicate / aggregate into ChangeRevisions
→ build a deterministic FactCapsule for every ChangeRevision
→ resolve ChangeInsight from validated cache / deterministic rules / bounded semantic batches
→ build an ultra-compact DirectionDigest for every ChangeInsight
→ synthesize first-class EnvironmentDirections from the full period corpus
→ build the user-facing Relevant projection
→ select a small Featured set for presentation only
→ generate the overall environment report
→ allow navigation to all relevant Changes
→ run evidence-heavy Deep Dive only when the user opens a Change/Direction
```

The primary result page should show, in this order:

1. A compact project identity and period selector, with project understanding in its own Settings view.
2. The **current EnvironmentDirections**: what patterns are forming, strengthening, weakening, or newly appearing, with supporting Changes and practical watch points.
3. A natural Chinese overall environment report synthesized from the full frozen period corpus.
4. A **Featured 5** set of Changes worth reading first. Featured is a presentation projection only; it does not automatically receive expensive deep analysis.
5. An obvious entry to all relevant Changes in that time range, where every row has at least a cheap shallow explanation of what changed and a lightweight project-relevance hint, regardless of whether that Insight came from validated cache, deterministic structured facts, or a semantic model call.

The scan-time semantic budget and the on-demand Deep Dive budget are different contracts. A Scan may observe/aggregate 300 Changes. All of them remain in the environment-synthesis horizon and receive a persisted shallow ChangeInsight; producing that Insight does not require an LLM when validated cache or deterministic structured facts are sufficient; the system must not spend evidence-research / multi-step impact-analysis budget on an arbitrary Top-K during the scan. **Featured 5 is selected from shallow intelligence for reading priority, not as a hidden deep-analysis queue.** Expensive evidence resolution, code-usage inspection, impact/action analysis, and optional verification start only after the user opens a Change or explicitly requests a deeper Direction analysis.

Keep **Observed corpus**, **Relevant projection**, **Featured projection**, and **On-demand Deep Dive state** distinct. Weak individual observations may still combine into a supported emerging direction, while raw source noise must not be mislabeled as project-relevant.

Project-owned activity and external-environment evidence are also distinct. Project activity may explain why an external Direction matters to the current codebase, but it must never establish that external Direction. Issue/discussion observations must retain a “reported/discussed” evidence posture rather than being promoted to shipped or confirmed behavior.

A compact change row/card should show only the user-facing change type, a short Chinese summary, and one sentence of likely project impact. **Numeric relevance/impact scores are internal ranking/audit data and are not a primary product-facing concept.** Full details are progressive disclosure: what happened, why it is relevant, affected modules/capabilities, recommended actions, Before/After when real evidence exists, sources/evidence, and optional audit reasoning/score/trace.

The normal Web product surface must not expose test/runtime implementation choices such as `mock-agent`, fixture mode, deterministic fallback mode, Harness variant names, raw SSE counts, or Agent-count internals. Those belong in explicit developer/audit surfaces. Normal live progress should use product milestones (collecting, aggregating, interpreting Changes, forming Directions, report ready), while raw Trace remains available behind Audit.

The Web frontend is a **P0 product asset and the primary expression of SignalHarness**, not a debugging shell over the runtime. Product contracts should be designed so the frontend can render stable user concepts (`Project`, `Direction`, `Change`, `Deep Dive`, `Monitoring`) without importing or mirroring Agent/Harness/scoring semantics. Backend implementation changes must not force product-surface vocabulary changes unless the domain contract itself changes.
## 3. Core domain model

The target business lifecycle is:

```text
Event / EventRevision
        ↓
Change / ChangeRevision
        ↓
ChangeInsight (cheap, scan-time, for every Change)
        ↓
Scan / ScanChange
        ├────────→ EnvironmentDirection
        ├────────→ Report / Featured projection
        └────────→ DeepDiveAnalysis (lazy, user-triggered)
```

- **Event**: one source-observed fact, such as a release, advisory, issue update, registry publication, PR merge, or web snapshot change.
- **EventRevision**: a later revision of the same source fact; identity and revision must not be conflated.
- **Change**: one underlying real-world environmental change that may be supported by multiple Events.
- **ChangeRevision**: new facts or corrections about the same Change.
- **ChangeInsight**: a cheap bounded interpretation produced for every ChangeRevision in the Scan: what changed, basic type/entity, lightweight project relation, attention class, uncertainty, and enough semantic structure to support Directions/search/display. It must not perform open-ended research or claim code-level impact without evidence.
- **ScanChange**: the frozen membership and presentation projection of a Change inside one Scan, including whether it belongs to Relevant or Featured views.
- **EnvironmentDirection**: a first-class, versioned cross-Change object describing a supported environment pattern such as `emerging`, `strengthening`, `stable`, or `weakening`, with supporting Change IDs, source diversity, project exposure hints, uncertainty, and what to watch next. Directions may continue across Scans rather than being recreated as disposable category labels.
- **DeepDiveAnalysis**: optional, cached, user-triggered analysis for one ChangeRevision (or later one Direction revision), tied to the Project Profile revision and analyzer version. This is where evidence resolution, code-usage/call-site inspection, impact analysis, recommended verification/actions, and selective verification belong.
- **Report**: a versioned product projection for one Scan, grounded in the full Change/ChangeInsight corpus and the resulting EnvironmentDirections.

**Featured 5 is never a persistence boundary or an automatic Deep Dive budget.** All relevant Changes remain queryable and shallowly understandable; Deep Dive state is created lazily on explicit user demand and reused when its pinned ChangeRevision/ProfileRevision/analyzer version is still valid.
## 4. Project Profile and Preference Engine

Project onboarding must not block on mandatory review. After authorization, SignalHarness should safely inspect manifests, lockfiles, runtime/config metadata, bounded representative paths, and approved repository metadata, then activate an initial profile immediately.

The effective profile is:

```text
Auto-discovered profile facts
+
Explicit user overrides/preferences
=
Effective Project Profile
```

Profiles are versioned. Historical Scans must retain the ProfileRevision used for analysis.

Users need a dedicated fast importance-control surface in addition to natural-language editing. The default interaction vocabulary should be simple and qualitative, for example: **Critical / Important / Normal / Low / Ignore**.

Importance can apply to dependencies, providers/APIs, runtimes/protocols, categories, sources, modules, or ecosystem topics. A user may also give lightweight feedback on a specific Change and choose whether it applies only to that Change, similar future changes for the same entity, or a broader category.

Natural-language instructions and UI controls must write to the same structured Preference/Override model. Explicit user preferences are reversible, auditable, and protected from automatic profile refresh.
## 5. `since_last` and time semantics

`since_last` means: **from this user's previous successful interactive environment query for this project to the frozen start time of the new query**.

Each Scan freezes an upper bound `U` when it is created and uses a half-open interval `[L, U)`. Processing end time must never replace `U`.

The following state must remain separate:

- interactive query checkpoint;
- source collection cursor/coverage;
- schedule checkpoint;
- notification delivery state;
- Inbox read state.

Successful manual queries ending at “now” may advance the interactive checkpoint. Pure historical lookbacks must not. Scheduled scans must not silently consume the user's manual `since_last` position.

A partial Scan remains readable but must not skip an unresolved required-source coverage gap. First-use `since_last` defaults to an explicitly disclosed 7-day lookback.

Late-discovered events and revisions are not silently dropped merely because their original publish time predates `L`; they may appear as explicit “late discovery / updated information” additions based on observed time and revision history.
## 5A. Analysis architecture: broad shallow scan, direction-first synthesis, lazy Deep Dive

The target scan path is **not** a fixed multi-Agent team. It is a bounded analysis pipeline with different cost tiers:

```text
L0  deterministic identity / revision / aggregation / source authority
 ↓
L1  FactCapsule + cache/deterministic SemanticRouter
     semantic misses → diversity-aware batched ChangeInterpreter with bounded concurrency
 ↓
     ChangeInsight for every Change
 ↓
L1.5 DirectionDigestBuilder + deterministic CorpusOrganizer
 ↓
L2  ONE EnvironmentSynthesizer call over every compact external DirectionDigest
    returns Directions + overall brief + Featured IDs
 ↓
     Relevant view + Featured 5 presentation

User opens a Change / Direction
 ↓
L3  DeepDiveAnalyzer
     ├─ EvidenceResolver
     ├─ lightweight Project Usage Resolver
     ├─ Impact + verification/action reasoning
     └─ optional SelectiveVerifier only when uncertainty warrants it
```

V1 deliberately combines Direction synthesis and report composition in one structured model call. No separate Narrative Agent or model-selecting frontend is required. Task/model routing is server-owned in `configs/intelligence_policy.yaml`.

The names above describe responsibilities, not a requirement that each box be a separately autonomous Agent. Prefer ordinary functions/services and bounded model calls when autonomy adds no value. There is no scan-time Supervisor LLM deciding which Agent runs next. The deterministic runtime owns stage order, batching, budgets, persistence, permissions, retries, and cache validity.

The current five-Agent implementation remains a regression baseline during migration, but its responsibilities move as follows:

- `SignalSupervisorAgent`: retire from the normal Scan hot path; deterministic routing replaces it.
- `ContextEvidenceAgent`: move to user-triggered Deep Dive / exceptional evidence repair.
- `ImpactAnalystAgent` + `ActionPlannerAgent` + `ImpactActionAnalyzerAgent`: consolidate around one DeepDiveAnalyzer responsibility; do not run them for a fixed Top-K every Scan.
- `SelectiveVerifierAgent`: keep only as conditional Deep Dive verification when uncertainty/evidence conflicts justify the extra cost.
- `LearningPolicyAgent`: remain outside the Scan hot path in calibration/replay.
- `ProjectNarrativeAgent`: replace its scan-time product role with full-corpus EnvironmentDirection synthesis and report composition; it must not summarize only a Featured subset.

Every Change receives a shallow `ChangeInsight`, but **not every Change must consume model tokens**. Build a `FactCapsule` first from deterministic source identity, version/date, authority and exact Project Profile matches. Validated cache wins first. Conservative structured cases may create a deterministic Insight; rich releases, advisories, issues, RSS, web diffs and ambiguous project relations remain semantic. The semantic ChangeInterpreter must be batch-friendly and schema-bounded, use diversity-aware deterministic batching, bounded concurrency, and split failed large batches without silently dropping work or degrading to one call per Change. It may output a short display summary and lightweight relevance/attention classification, but it may not fabricate source evidence, code reachability, exact affected modules, or detailed remediation.

The user-readable `ChangeInsight` and strong-model input are different contracts. A deterministic `DirectionDigestBuilder` compresses each Insight to a compact fact/entity/kind/date/posture/topic/project-relation/source row, while full Evidence remains persisted for Change detail/Deep Dive. `CorpusOrganizer` may add count indexes and ordering but must never summarize away Changes.

Current implementation and deliberate limits are documented in `docs/ENVIRONMENT_INTELLIGENCE_V1.md`; the target below must not be read as a claim that every capability is implemented.

EnvironmentDirection synthesis should normally receive **every compact external DirectionDigest for the Scan** so the model can connect weak signals across batches. If the digest corpus exceeds the selected model's safe context/budget, use an explicit provenance-preserving hierarchical reduction once implemented; until then, degrade truthfully rather than silently truncating to Featured or Top-K.

## 6. Memory and state boundaries

SignalHarness should not treat “memory” as one large JSON blob passed to Agents. Persistent state is separated by lifecycle and authority.

### Domain State

Events, revisions, Changes, evidence, Project Profiles, ProjectImpacts, Scans, and Reports. Public world facts may be reused across projects; project-specific impact never is.

### Run State

Current Scan stage, retries, budgets, leases, temporary tool observations, and loop state. This state is durable enough for recovery when required, but it is not long-term semantic memory.

### Learned State

Explicit preferences, feedback, episodic examples, validated learned preferences, and versioned policy revisions.

### Audit State

Trace, model/provider version, prompt/context version, tool calls, latency, cost, errors, fallback, and verification metadata. Audit is queryable but not injected into normal model context by default.

Private repository/local/team facts are scoped to the authorized connection/project boundary. In a future multi-user system, user query checkpoints, read state, and personal notification preferences are scoped by project + user/consumer.
## 7. Learned-state authority and memory types

Within learned/profile state, authority matters. Default precedence is:

```text
Explicit user preference
> verified project fact
> verified external fact
> validated learned preference
> consolidated pattern
> model inference
```

A lower-authority inference must never silently override a higher-authority explicit preference.

For analysis, learned memory is further understood as:

- **Semantic**: durable project facts and preferences.
- **Episodic**: prior similar Changes, past impact judgments, user feedback, and observed outcomes.
- **Procedural**: ranking policy, prompts, evidence rules, source-authority rules, and notification policy.

Procedural memory is the most sensitive. It cannot be directly rewritten by an ordinary Scan Agent; changes follow proposal, replay/evaluation, versioning, and controlled promotion.

Long history is consolidated rather than blindly appended to every prompt. Raw feedback and episodes remain auditable, while compact durable lessons/preferences are what normal analysis retrieves.
## 8. Analysis architecture: Brain / Session / Hands

The target architecture separates three concerns:

- **Brain**: semantic reasoning such as evidence research, project impact/action analysis, and selective verification.
- **Session / Orchestration**: Scan state machine, persistence, budgets, retry, coverage, context packet construction, checkpoints, and trace.
- **Hands**: GitHub, local Git, package registries, RSS/Atom, official APIs/changelogs, OSV/security, and Web Snapshot/Diff connectors.

Model/harness upgrades should be replaceable without rewriting durable data and connectors. Connector upgrades should not rewrite analysis policy. Scan recovery must not depend on a model retaining hidden conversational state.

The deterministic outer pipeline owns collection, identity, revisions, aggregation, permissions, window semantics, persistence, ranking policy, notification eligibility, and transaction boundaries.

LLMs are used only where semantic reasoning has measurable value.

## 9. Context Packet as a first-class contract

Normal impact analysis receives a bounded `ContextPacket`, not the entire project history. It should contain only high-signal information required for the current Change, such as ChangeRevision, EvidencePacket, compact project profile, relevant modules/entities, relevant preferences, current dependency/API facts, a small number of similar historical episodes when useful, and policy constraints.

Context items should carry source/scope/revision/confidence/authority metadata where applicable. Context construction is versioned and observable so Analyzer variants can be compared on frozen inputs.
## 10. Agent and loop strategy

The current five-Agent implementation remains a working baseline, not a permanent product requirement.

The current preferred direction to validate is:

```text
Deterministic Triage
→ Evidence Resolver
→ optional bounded EvidenceResearcher
→ ImpactActionAnalyzer
→ deterministic scoring/ranking
→ optional SelectiveVerifier for high-risk or low-confidence cases
```

The outer product workflow is not an open-ended Agent loop. Agentic loops are local, bounded, and justified only when the model must decide whether more evidence is required.

Evidence research stops when evidence is sufficient, authoritative sources are exhausted, no new useful evidence is found, permissions prevent further access, or token/tool/time budgets are exhausted. Unresolved conflicts terminate as `uncertain` rather than looping indefinitely.

Agent-to-Agent repair uses an explicit finite escalation graph with bounded repair rounds. Free-form recursive handoffs are not an MVP requirement.

Whether Supervisor, separate ActionPlanner, EvidenceResearcher, SelectiveVerifier, episodic retrieval, or any other harness component survives long-term is decided by measured product value rather than architectural aesthetics.
## 11. Preference and Calibration engines

Learning is split into two different trust paths.

### Preference Engine

Explicit user choices are structured, immediate, reversible, and project-scoped. UI controls and natural-language instructions write to the same preference model. This is not model self-learning.

### Calibration Engine

The system may later use feedback and real outcomes to propose better learned preferences, ranking rules, prompts, routing, or evidence policy. Calibration is asynchronous and outside the Scan hot path.

The target promotion ladder is:

```text
Raw feedback / outcome
→ episode
→ consolidated pattern
→ candidate preference/policy
→ historical replay
→ shadow evaluation
→ proposal
→ versioned active revision
```

Low-risk learned preferences may eventually support controlled promotion; sensitive procedural-policy changes require stronger gates. The system must distinguish “you explicitly set this” from “SignalHarness learned/suggested this”.

The current `LearningPolicyAgent` is therefore a baseline implementation artifact, not a requirement that every Scan ends with a fifth learning Agent call.
## 12. Evaluation-driven development

SignalHarness should use tests for correctness and Evals for quality choices. Product invariants are not delegated to Evals; uncertain quality choices are.

Examples that are fixed by product semantics include Event ≠ Change ≠ ProjectImpact, Top not deleting All, explicit preference authority, checkpoint separation, and LLMs not owning transaction success.

Examples that should be measured include Agent count, Supervisor value, Evidence frequency, Impact+Action merge, Verifier value, episodic retrieval, ranking weights, model choice, and context size.

Analyzer/Harness comparisons use a frozen corpus so variants see the same ChangeRevision, EvidencePacket, ProjectProfileRevision, Preference set, budgets, and tool availability. Results record analyzer/model/prompt/context/policy versions.

Eval families should grow around real product risks:

- collection/coverage;
- revision and aggregation correctness;
- relevance recall / false positives / false negatives;
- impact/module correctness and citation support;
- action usefulness / overstatement;
- Top ranking usefulness;
- report coverage and factual consistency;
- calibration improvement and regression safety.

Early product development should keep Evals lightweight: deterministic edge cases, a modest frozen real-world corpus, a small number of Analyzer variants, and human/LLM-assisted spot checks. Do not build a benchmark platform larger than the product before real users exist.
## 13. Source layer and aggregation

Source type is a collection mechanism, not a user-facing change category. The target source layer includes:

- own-project GitHub and local Git: commits, merged PRs, releases, issues, Actions/security-relevant changes where appropriate;
- dependency/upstream repositories: releases, important bugs, migrations, deprecations, security;
- package registries such as PyPI/npm first, then others as real projects require;
- RSS/Atom for official changelogs, engineering/security blogs, and selected expert feeds;
- official API/changelog/status/model/spec endpoints;
- Web Snapshot/Diff for important pages without reliable structured feeds;
- security sources such as OSV/GitHub advisories/CVE-related data matched against project dependency facts;
- bounded external ecosystem sources with lower default authority.

Aggregation is not a source. Multiple source observations that describe the same underlying event become one Change while retaining all evidence links/revisions. Deterministic identity keys such as package+version, advisory aliases, repo+release, or PR relationships take priority over semantic similarity.

The first observation of a Web Snapshot establishes a baseline; the system must not invent historical Before/After data that it never observed.

Each connector needs identity/revision semantics, pagination/history limits, coverage diagnostics, bounded retry/rate-limit behavior, and truthful partial-failure reporting before it counts as a completed product source.
## 14. Product projections and interfaces

The overall Chinese environment report is a first-class product artifact generated from the full relevant Scan set, not merely a concatenation of Top items. Programmatic counts/statistics should be computed deterministically before narrative synthesis.

All Relevant Changes is a frozen Scan projection and supports stable pagination, filtering, sorting, and search. Raw source noise remains available for audit/debugging but is not the normal product list.

Copy/export should share one Markdown renderer with at least three useful modes: single change, overall report, and highlighted/Top changes.

Web, CLI, REST API, and MCP must expose the same core Scan service rather than reimplementing business logic. The current interface priority is **CLI-first for developers and shell-capable coding Agents**, Web for human intelligence reading/control, REST for programmatic integration, and MCP as a thin optional Agent adapter when tool discovery or a no-Shell client makes it useful.

The CLI should be deliberately Agent-friendly: stable commands, machine-readable JSON output for core project/scan/change/report operations, stdout reserved for requested data, and diagnostics/logging separated from structured output. Coding Agents that already have Shell access should not require MCP merely to consume SignalHarness.

MCP remains supported rather than becoming the product core. It must eventually be able to start a real fresh scan, retrieve long-running scan status/results, list project changes, and fetch change details by delegating to the same application services used by CLI/REST/Web. A scan-starting MCP tool is not read-only: it may create persistent state, perform network requests, and incur model cost. Side-effect and idempotency semantics must be truthful.

## 15. Scheduling and notifications

Manual queries, scheduled scans, Inbox state, and external notification delivery are separate product concerns built on the same durable Scan result.

The target first continuous-monitoring slice is a persistent schedule mechanism plus SignalHarness Inbox and one real external delivery path. A generic signed Webhook or ntfy are reasonable first choices depending deployment/user needs; Apprise is a later option when multi-channel breadth is genuinely required.

SignalHarness owns notification policy, outbox/delivery state, retry, cooldown/digest logic, and deduplication. Channel libraries/services own transport. Local `alerts.md/json` artifacts alone do not count as external notification delivery.
## 16. Architecture restraint / non-goals

Do not add framework or infrastructure complexity merely to make the project look more agentic. The current preferred baseline is a modular monolith with typed contracts, durable relational state when required, and bounded background execution.

Not first-line requirements:

- LangGraph/CrewAI/AutoGen orchestration;
- generic vector RAG or graph database;
- Redis/Celery/Kafka/Temporal-style distributed runtime;
- microservice decomposition;
- generic web-crawling research agents;
- automatic code modification or upgrade PRs;
- fixed five-Agent product identity;
- broad SaaS team/role/tenant complexity before a real deployment requires it.

These can be revisited only when measured product or operational needs justify them.

## 17. Decision classes

Future planning should label decisions as one of:

- **Confirmed product decision**: product/business/state/safety invariant that later Agents must preserve.
- **Current architecture direction**: preferred implementation direction supported by current evidence, but revisable after construction evidence.
- **Experimental / to validate**: competing quality choices decided through Harness/Eval comparisons.

The number of Agents, SelectiveVerifier value, episodic retrieval mechanism, exact ranking formula, model/provider, vector search, database scaling point, and durable workflow technology remain experimental/conditional choices rather than confirmed requirements.
## 18. Final product acceptance picture

A mature first product release should let a user:

1. connect a real GitHub/local project;
2. immediately receive an auto-generated, editable project profile;
3. quickly mark entities/categories/sources as Critical/Important/Normal/Low/Ignore;
4. query since-last, 24h, 7d, 30d, or custom windows with truthful coverage;
5. process hundreds of real source observations without losing relevant Changes to Top-K budgets;
6. first read a natural Chinese overall environment report, then Top changes, then All Relevant Changes;
7. open a Change to see supported impact/actions/evidence and copy it cleanly;
8. trigger the same capability through a first-class CLI and the same core service through Web/REST/MCP where appropriate;
9. schedule scans and receive important results in Inbox plus one real delivery channel;
10. accumulate feedback/outcomes so later Calibration can be tested rather than simulated.

The durable engineering principle is: **stable product contracts and state boundaries first; uncertain quality choices are replaceable variants evaluated on frozen evidence.** Harness components are allowed to disappear when newer models or simpler methods achieve equal or better product outcomes.