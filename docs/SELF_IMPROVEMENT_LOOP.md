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
`configs/signal_policy.yaml`, skill files, or `configs/watchlist.yaml`. Policy
application remains a separate, explicit `calibrate --apply` operation guarded
by user confirmation.

Prompt construction never places complete feedback history at the front of the
system prompt. `PromptContextBuilder` compresses feedback count and recent notes
into semi-stable memory context; current feedback and task data remain dynamic.
Replay and scripted evals run without a real LLM key.
