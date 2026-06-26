# SignalHarness

SignalHarness 是一个基于 OpenHarness 的 LLM-native 多 Agent 信号情报
Harness。LLM Agent 是主要智能来源；确定性 Python 组件负责 schema
校验、评分约束、权限、fallback、回放评估与可追踪性。

固定的五个 Agent 是：

1. `SignalSupervisorAgent`
2. `ContextEvidenceAgent`
3. `ImpactAnalystAgent`
4. `ActionPlannerAgent`
5. `LearningPolicyAgent`

Memory 是基础设施，不是 Agent。它由 `ProjectMemory`、`SignalMemory`、
`FeedbackMemory` 和 `PolicyMemory` 组成。

## 三种模式

```bash
# 确定性 fallback，不是真正多 Agent
uv run signal-harness scan \
  --fixture examples/signal_harness/sample_events.json \
  --mode demo

# 无需 Key，但完整经过五次 LLM Agent 调用链
uv run signal-harness scan \
  --fixture examples/signal_harness/sample_events.json \
  --mode mock-agent

# 真实 OpenHarness provider
LLM_API_KEY=... uv run signal-harness scan \
  --fixture examples/signal_harness/sample_events.json \
  --mode agent
```

`ImpactAnalystAgent` 不能输出最终分数。最终分数由确定性 base score、LLM
semantic relevance、evidence confidence 和 policy 权重共同约束生成。

## 反馈与学习

```bash
uv run signal-harness feedback \
  --signal-id demo-001 \
  --label useful \
  --note "checkpoint and memory signals are important"

uv run signal-harness calibrate --mode mock-agent
```

LearningPolicyAgent 只生成待审批的 policy、skill、watchlist proposal 和
replay evaluation，不会自动修改正式配置。

## 验证

```bash
uv run --extra dev python -m pytest tests/signal_harness -q
uv run --extra dev ruff check src/signal_harness tests/signal_harness
uv run --extra dev mypy src/signal_harness
uv build
```

SignalHarness 复用 HKUDS/OpenHarness 的模型客户端、消息协议、工具、技能、
插件与权限基础。原始 MIT License 和归属声明见 [LICENSE](LICENSE) 与
[NOTICE.md](NOTICE.md)。
