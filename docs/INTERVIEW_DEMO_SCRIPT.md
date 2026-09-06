# SignalHarness 面试展示讲稿

这份讲稿用于把 SignalHarness 讲成一个“AI Agent / Agent Harness / 工程可解释性”项目，而不是普通聊天机器人、普通爬虫或静态 dashboard。

## 30 秒版本

SignalHarness 是一个 project-centric signal intelligence harness。它监听 GitHub、RSS、Web change 或 fixture 输入，把外部变化路由给五个 Agent：Supervisor 做分类和路由，ContextEvidence 做证据验证，ImpactAnalyst 判断项目影响，ActionPlanner 生成安全行动建议，LearningPolicy 产出 review-only 学习提案。

关键点是：LLM 不直接执行工具、不直接写文件、也不能决定最终分数。Python runtime 负责 schema validation、permission guard、tool allowlist、scoring、fallback、trace 和本地 dashboard/report。所以它不是聊天机器人，而是一个可审计的 Agent Harness。

## 3 分钟版本

SignalHarness 解决的是一个真实工程问题：小团队每天会面对大量 GitHub issue/release、RSS、API 更新和生态变化，但真正影响项目的信号很少。普通爬虫只能收集信息，普通 dashboard 只能展示信息，普通 chatbot 又缺少可审计边界。SignalHarness 的目标是把“外部信息”变成“项目上下文里的可解释判断”。

输入层支持 GitHub、RSS、Web change 和 fixture。数据进入后会经过 source collection、normalization、deduplication、noise filter 和 clustering。然后进入五个 Agent 的 routed workflow：

1. `SignalSupervisorAgent`：判断每条 signal 是否值得分析，以及需要哪些下游 Agent。
2. `ContextEvidenceAgent`：请求只读工具，读取 Python runtime 返回的 tool observations，合成证据。
3. `ImpactAnalystAgent`：结合项目 profile 判断 affected modules、semantic relevance、impact reasoning。
4. `ActionPlannerAgent`：只提出 review / investigation / documentation 这类安全行动建议。
5. `LearningPolicyAgent`：读取 memory infrastructure，产出 policy / skill / watchlist 的 review-only proposal。

Python runtime 是安全边界：它负责 schema validation、permission guard、tool allowlist、scoring、fallback、trace、local dashboard 和 report。LLM 负责 classification、evidence synthesis、impact reasoning、action planning 和 learning proposal，但不拥有外部副作用。

如果 fallback 出现，SignalHarness 会明确展示，而不是隐藏。因为这类系统真正重要的是 auditability：面试时我会强调 fallback 不是“失败要掩盖”，而是系统在模型不稳定时仍能给出可追踪、保守、安全的审计输出。Learning 也是 review-only，不会自动改配置，因为高风险配置变更必须有人审查。

我现在会优先展示 regression evidence：`resume-v1` 有 40 个标签 case，当前 offline mock-agent contract gate 是 decision/category 40/40，priority precision/recall 都是 100%，FPR/FNR 都是 0%。第一版 baseline 只有 75% decision accuracy、28.57% priority recall，回归集真实暴露了 category 权重重复、mock context 漏接和否定语义误判，修复后才达到当前结果。这个数字只代表 SignalHarness 项目 contract，不包装成通用 LLM benchmark。

## 5 分钟版本

SignalHarness 可以从三个角度讲。

第一，它是一个 Agent Harness。它不是开放式 ReAct loop，也不是让模型随便调用工具。它固定了五个 Agent 角色，并让 Python runtime 管住所有可验证边界：schemas、tools、permissions、scoring、fallback、trace、local outputs。这样设计的好处是面试官能看到每一步为什么发生、模型说了什么、工具执行了什么、哪些结果被 fallback 或 audit completion 接管。

第二，它是 project-centric signal intelligence。输入不是用户聊天，而是外部变化源：GitHub repositories、RSS/Atom feeds、Web change fixtures、offline fixtures。项目 profile 里有 dependencies、critical modules、focus keywords、monitored ecosystem。系统会把“外部事件”转成“对当前项目的影响判断”，例如 provider API compatibility、tool calling behavior、schema validation、security/supply-chain、source health、evaluation signal。

第三，它强调工程可解释性。Dashboard 不是为了好看堆 section，而是展示运行健康：Executive Summary、Signal Summary、No high-priority signals / Top observed signals、Source health、Tool health、Model/profile/limits、Agent trace/tools、Score breakdown、review-only learning。Trace 里能看到 tools_requested、tools_executed、permission checks、schema retry、fallback、source task health。

这里有几个边界是我会主动讲的：

- LLM 不能直接执行工具，只能提出 tool requests；Python tool executor 决定是否允许。
- LLM 不能直接写文件；所有 outputs 都由 runtime/report writer 生成。
- LLM 不能决定最终分数；最终 score 由 deterministic base、semantic relevance、evidence confidence 和 policy multiplier 组合。
- Learning proposal 不能自动 apply；必须 review，且高风险或 replay gate failed 的 proposal 不会自动生效。

工程证据可以分三层讲：第一层是 40-case regression gate，验证产品决策；第二层是 model-eval，验证 provider schema/retry/fallback/tool/latency/token/cost contract；第三层是 service/MCP/Docker，证明 Harness 能作为真实接口被外部 Agent 或服务调用。历史 live provider 结果仍可以作为 dated snapshot 展示，但不会把一次 live run 当成长期质量证明。


## 现场 Demo 推荐顺序

先启动：

```bash
uv run signal-harness serve --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000/demo`。

1. 先指顶部 40-case regression evidence 和 5 个 read-only MCP tools，说明这是当前 committed/CI evidence，不是通用 LLM benchmark。
2. 点击 **Run Golden Demo**。stream-run 在 SSE 连接建立后才启动，因此五 Agent、Tool Guard、Trace 的变化来自真实 runtime event，不是前端定时器。
3. 点击 `ContextEvidenceAgent`，展示 schema valid、requested/executed tools、permission checks、fallback/retry。
4. 看右侧 Final decisions，解释 LLM 提供 semantics，但 Python owns the final score/decision。
5. 下拉到 Live trace ledger，说明同一份 TraceRecorder 同时写审计 JSON 和推 SSE；断线后 workflow 继续，浏览器可用 `Last-Event-ID` 补事件。
6. 最后说明 `/mcp`、同步 REST 和 Docker 都复用同一 Workflow；SSE 是 in-process observability/demo layer，不冒充 Redis/Celery durable queue。

如果时间只有 2-3 分钟，只展示 `/demo` 的一次 mock-agent run + Evidence Tool Guard + Final decisions。终端 regression command 作为追问时的第二证据。

## 面试官可能追问

### 为什么要做这个项目？

因为真实团队的信息来源非常碎：GitHub issue/release、provider API update、RSS、security blog、framework changelog 都可能影响项目。人工看很累，纯爬虫没有项目判断，纯 LLM 又难以审计。SignalHarness 把这些合成一个项目上下文驱动的 Agent Harness。

### 和普通 RAG / 爬虫 / dashboard 有什么区别？

爬虫只收集，dashboard 只展示，RAG 通常回答用户问题。SignalHarness 是 batch-oriented Agent Harness：它有 routing、tool-use loop、impact assessment、guarded scoring、trace、fallback 和 review-only learning。重点不是“问答”，而是“可解释地把外部变化转成项目决策信号”。

### 为什么不用 LangGraph 直接做？

SignalHarness 借鉴了 supervisor routing、handoff 可解释性和 trace 思想，但这个项目更想展示底层 harness 能力：Python runtime 怎么掌控 schema、tools、permissions、scoring 和 local outputs。引入 LangGraph 会让面试焦点变成框架使用，而不是我对 Agent Harness 边界的设计。

### fallback 是不是说明模型不稳定？

fallback 说明真实模型路径可能遇到 schema、timeout、provider 或 coverage 问题。成熟系统不应该假装这些不会发生，而应该把 fallback 做成 guardrail，并在 trace/dashboard 明确展示。v4 live run 的 fallback_count=0，但如果未来出现 fallback，也应该被看见。

### 为什么不自动学习？

因为 learning proposal 可能改变 scoring、watchlist 或 policy。自动应用会制造隐性反馈回路，尤其在高风险类别上不安全。SignalHarness 把 Memory 作为 infrastructure，LearningPolicyAgent 只能提出 review-only proposal，最终应用必须显式批准。

### 这个项目如何迁移到别的项目？

主要替换 `configs/project_profile.yaml`、`configs/watchlist.yaml` 和 `configs/signal_policy.yaml`。Agent workflow、tool guard、trace、dashboard、model profiles 可以复用。也可以通过 fixture 先离线评估，再启用 live watchlist。

### 线上化还缺什么？

缺生产级 auth、multi-tenant state、任务调度、长期存储、告警审批流、成本预算、provider quota 管控、可观测性平台集成和更丰富 eval fixtures。但这些都可以在现有 harness 边界上逐步加，不需要推翻核心架构。
