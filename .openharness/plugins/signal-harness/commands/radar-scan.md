---
description: Run the SignalHarness LLM-native radar workflow and explain the structured output.
argument-hint: "[--mode demo|mock-agent|agent] [--fixture FILE] [--since ISO_TIME]"
---

Run `signal-harness scan $ARGUMENTS` from the project root. Then inspect
`outputs/run_summary.txt`, `outputs/radar_digest.md`, and
`outputs/task_trace.json`. Use `mock-agent` to validate the full five-Agent
chain offline. Report failed sources and LLM fallback explicitly. Do not edit
code, apply policy, create issues, or send notifications.
