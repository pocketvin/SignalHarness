# SignalHarness Architecture

SignalHarness 是一个 project-centric signal intelligence Agent Harness。它把外部变化源转成可审计的 assessment、digest、dashboard、trace 和 review-only learning proposal，并通过 CLI、REST、SSE Golden Demo 与只读 MCP 暴露同一套 domain/runtime 能力。

## Workflow flowchart

```mermaid
flowchart TD
    A["External Sources<br/>GitHub / RSS / Web change / fixture"] --> B["Source Collection"]
    B --> C["Normalization"]
    C --> D["Deduplication"]
    D --> E["Noise Filter"]
    E --> F["SignalSupervisorAgent"]
    F --> G["ContextEvidenceAgent"]
    G --> H["ImpactAnalystAgent"]
    H --> I["ActionPlannerAgent"]
    I --> J["LearningPolicyAgent"]
    J --> K["Guarded Assessment"]
    K --> L["Alerts"]
    K --> M["Digest"]
    K --> N["Dashboard"]
    K --> O["Trace"]
    K --> P["Review-only Learning"]
    K --> R["Regression / Provider Eval"]
    K --> S["CLI / REST / MCP"]
    O --> T["Trace Event Sink / SSE / Golden Demo"]

    Q["Python Runtime<br/>schema validation / permissions / scoring / fallback / file writes"] -. guards .-> F
    Q -. guards .-> G
    Q -. guards .-> H
    Q -. guards .-> I
    Q -. guards .-> J
```

## Real agent run sequence

```mermaid
sequenceDiagram
    participant User as User CLI
    participant Workflow
    participant Provider
    participant Agents as Five Agents
    participant Tools as Tool Executor
    participant Trace as Trace Recorder
    participant Dashboard as Dashboard Writer

    User->>Workflow: signal-harness scan --mode agent
    Workflow->>Trace: record load_config / collect_signals
    Workflow->>Provider: structured call: SignalSupervisorAgent
    Provider-->>Workflow: SupervisorOutput
    Workflow->>Trace: record schema / route metadata
    Workflow->>Provider: structured call: ContextEvidenceAgent tool plan
    Provider-->>Workflow: EvidenceToolPlan
    Workflow->>Tools: execute allowed read-only tools
    Tools-->>Workflow: ToolObservation objects
    Workflow->>Trace: tools_requested / tools_executed / permission_checks
    Workflow->>Provider: structured call: ContextEvidenceAgent final evidence
    Provider-->>Workflow: ContextEvidenceOutput
    Workflow->>Provider: structured call: ImpactAnalystAgent
    Provider-->>Workflow: ImpactOutput
    Workflow->>Provider: structured call: ActionPlannerAgent
    Provider-->>Workflow: ActionOutput
    Workflow->>Provider: structured call or review noop: LearningPolicyAgent
    Provider-->>Workflow: LearningPolicyOutput
    Workflow->>Workflow: guarded scoring and decision mapping
    Workflow->>Trace: record fallback / retry / audit completion if any
    Workflow->>Dashboard: write local dashboard.html and reports
    Dashboard-->>User: local files under outputs/
```

## 四层安全边界

1. LLM 不能直接执行工具  
   Agent 只能输出 structured tool requests。Python runtime 检查 allowlist、permission policy、budget 和 read-only constraints，再决定是否执行。

2. LLM 不能直接写文件  
   LLM 只能返回 schema-validated JSON。`outputs/`、trace、digest、dashboard、proposal snapshots 都由 Python runtime 或 report writer 写入本地文件。

3. LLM 不能决定最终分数  
   `ImpactAnalystAgent` 提供 semantic relevance 和 impact reasoning，但没有 authoritative `final_score` 字段。最终分数由 deterministic scoring、semantic relevance、evidence confidence 和 policy multiplier 组合。

4. Learning proposal 不能自动 apply  
   `LearningPolicyAgent` 只能产出 review-only proposal。高风险 proposal 或 replay gate failed proposal 不会自动应用；需要显式 review 和 approval。

## 核心文件路径

- `configs/project_profile.yaml`  
  项目上下文：技术栈、真实 dependencies、monitored ecosystem、critical modules、focus keywords。

- `configs/watchlist.yaml`  
  live source watchlist：GitHub repos、RSS feeds、Web change sources。

- `configs/signal_policy.yaml`  
  deterministic scoring weights、category weights、thresholds、tool allowlist、permission policy。

- `src/signal_harness/runtime/workflow.py`  
  主 workflow：source collection、normalization、deduplication、noise filter、event limit、Agent run、report writing。

- `src/signal_harness/agent_integration/runner.py`  
  五 Agent runner：controlled tool-use loop、schema retry、repair boundary、audit completion、LearningPolicy handling。

- `src/signal_harness/agent_integration/scoring_bridge.py`  
  将 Agent outputs 转成 guarded `SignalAssessment`，并由 Python runtime 计算 final decision。

- `src/signal_harness/evals.py`
  两类 eval：40-case 产品 regression gate 与 provider contract/model eval。

- `src/signal_harness/mcp_server.py`
  五个结构化只读 MCP tools；读取 project context、signal history、assessment、trace 和 feedback，且不能绕过 permission policy。

- `src/signal_harness/service.py`
  FastAPI REST + SSE + MCP Streamable HTTP 服务层；每次 run 隔离 output/state，复用同一 Workflow。

- `src/signal_harness/service_streaming.py`
  in-process stream-run manager：首个 SSE subscriber 启动 queued workflow，保留 event-id 历史用于重连回放；断开浏览器不取消 run。

- `src/signal_harness/ui/demo.py`
  FastAPI 直接托管的 Golden Demo 单页。UI 只消费真实 trace append/update 事件，不维护假的 Agent 执行状态。

- `src/signal_harness/ui/dashboard.py`  
  静态本地 dashboard writer。展示 summary、signals、source/tool health、model/profile/limits、trace、token/cost/latency、score breakdown、learning。

- `outputs/dashboard.html`  
  本地 dashboard 产物。用于 demo，不提交。

- `outputs/task_trace.json`  
  本地 trace 产物。记录 Agent calls、schema/fallback/retry、tool requests/executions、permission checks、source task health。

## 面试展示重点

SignalHarness 的价值不是“又做了一个 dashboard”，而是展示 Agent Harness 的工程边界：LLM 做推理，Python 做约束；模型输出可审计，工具使用可追踪，fallback 不隐藏，learning 不自动改配置。

## Evaluation and observability

SignalHarness 把“模型是否稳定”和“产品行为是否正确”拆成两层。`regression-eval --enforce` 使用 40 个标签 case 验证 decision/category、priority precision/recall、FPR/FNR；`model-eval` 则验证 provider schema、retry/fallback、tool errors、repair、latency、provider-reported token usage 和 estimated cost。二者都不是通用 LLM leaderboard。

当前 `resume-v1` offline regression suite 的 committed acceptance 是 40/40 exact decision、40/40 exact category、priority precision/recall 100%、FPR/FNR 0%。这些数字来自项目特定 contract corpus。

## Service and deployment boundary

`signal-harness serve` 启动 FastAPI，提供 health、同步 run、trace、assessment、signals、feedback，以及 `/demo` Golden Demo；`/stream-runs/{id}/events` 使用 SSE 推送同一 `TraceRecorder` 的真实 append/update。原 `POST /runs` 仍同步；stream-run 是进程内 asyncio task，不是持久化队列。首个 SSE subscriber 才启动 queued run，断线后任务继续，`Last-Event-ID` 可补发内存事件历史。服务重启后 live subscription history 不恢复。Docker 镜像运行相同入口并包含 `/health` healthcheck。

MCP 是只读第二入口，不是新的副作用平面。所有可写行为仍由原有 Workflow、permission guard 和 learning gate 控制。
