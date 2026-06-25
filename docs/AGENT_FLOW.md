# SignalHarness Agent Flow

The true Agent flow is the fixed five-call chain documented in
[LLM_AGENT_ARCHITECTURE.md](LLM_AGENT_ARCHITECTURE.md).

```text
SignalSupervisorAgent
  -> ContextEvidenceAgent
  -> ImpactAnalystAgent
  -> ActionPlannerAgent
  -> deterministic score + permission guard
  -> LearningPolicyAgent observation
```

`demo` uses the legacy deterministic specialists as fallback. `mock-agent` and
`agent` use the LLM Agent chain.
