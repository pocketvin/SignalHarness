# Self-Improvement Loop

Memory is infrastructure, not an Agent.

- `ProjectMemory` loads the project profile and watchlist.
- `SignalMemory` stores seen IDs, duplicate hashes, and historical assessments.
- `FeedbackMemory` stores useful, not-useful, false-positive, missed-signal, and
  too-generic judgments with notes.
- `PolicyMemory` exposes the active policy, versions, and latest proposal.

`LearningPolicyAgent` receives a read-only snapshot of these stores and returns:

- `policy_update_proposal.json`
- `skill_update_proposal.md`
- `watchlist_update_proposal.json`

The deterministic replay evaluator compares the old and proposed policy on
historical signals and feedback and writes:

- `old_precision_proxy`
- `new_precision_proxy`
- `false_positive_reduction`
- `missed_signal_reduction`
- `recommendation`

All proposals require approval. Calibration does not modify
`configs/signal_policy.yaml`, skill files, or `configs/watchlist.yaml`.
`calibrate --apply` goes through the same local staging gate as
`learning-stage` / `learning-review` / `learning-apply`: without `--yes` it
stages and prints review/apply instructions; with `--yes`, only low-risk
proposals with a passing replay gate can be applied. High-risk or replay-failed
proposals remain staged for human review.

Prompt construction never places complete feedback history at the front of the
system prompt. `PromptContextBuilder` compresses feedback count and recent notes
into semi-stable memory context; current feedback and task data remain dynamic.
Replay and scripted evals run without a real LLM key.
