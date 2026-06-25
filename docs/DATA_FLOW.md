# SignalHarness Data Flow

```text
collect -> normalize -> deduplicate
  -> SignalSupervisorAgent
  -> ContextEvidenceAgent
  -> ImpactAnalystAgent
  -> ActionPlannerAgent
  -> deterministic base score and guarded blend
  -> permission checks
  -> reports and LLM task trace
```

Feedback is appended to `FeedbackMemory`. Calibration snapshots all four memory
stores, invokes `LearningPolicyAgent`, writes three review-only proposals, and
runs deterministic replay evaluation. Existing reports and formal
configuration are not silently rewritten.
