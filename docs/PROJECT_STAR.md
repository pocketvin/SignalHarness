# SignalHarness STAR 面试材料

## STAR 1：为什么做 SignalHarness

### Situation
开发团队每天面对 GitHub issues/releases、RSS、依赖更新、provider API 变化和安全博客。信息很多，但真正影响项目的变化很少。普通爬虫只能收集，普通 dashboard 只能展示，普通 chatbot 又缺少可审计边界。

### Task
做一个项目上下文驱动的 Agent Harness：理解当前项目 profile，判断外部变化是否影响项目，并输出可追踪的 assessment、trace、dashboard 和 review-only learning proposal。

### Action
实现 source collection、normalization、deduplication、noise filter、five-Agent routed workflow、guarded scoring、trace 和 dashboard。五个 Agent 负责 routing、evidence、impact、action 和 learning；Python runtime 负责 schema、permission、tool budget、final score、fallback 和 outputs。

### Result
系统可以把外部变化转成 ignore/save/alert/action_required，并把模型、工具和 fallback 行为留在 trace 中。后来又增加 regression eval、MCP、REST、Docker 和 token/cost observability，使它从 CLI demo 变成可复用 Harness。

### 30 秒回答
我做 SignalHarness 是因为真实团队的信息噪声很大。它不是聊天机器人，而是一个 Agent Harness：外部信号经过五个 Agent 分工推理，但 schema、工具权限、最终 scoring、fallback 和 learning apply 都由 Python 控制，所以每个判断都能追踪和复现。
## STAR 2：处理模型不稳定与 fallback

### Situation
真实 provider 可能出现 schema invalid、timeout、tool error 或 retry。如果系统把这些隐藏，dashboard 会显得稳定但不可审计。

### Task
让 Harness 在模型失败时仍能给出保守输出，同时明确区分 schema retry、deterministic fallback、whole-run timeout 和 skipped-stage audit completion。

### Action
保留严格 Pydantic contracts 和一次 schema retry；provider timeout 与 whole-run timeout 分开记录；tool requests 先经过 allowlist、permission 和 budget；fallback 只补审计默认值，不冒充完整 LLM reasoning。Dashboard 和 model-eval 汇总 retry/fallback/tool/latency 状态。

### Result
模型健康状态不再被隐藏，provider contract 可以用同一套指标比较。真实 token usage 有 provider 数据时进入 trace，mock-agent 不伪造 token；定价 profile 可进一步计算 estimated cost。

### 30 秒回答
我没有把 fallback 当成要隐藏的错误，而是把它设计成 Harness guardrail。模型负责语义，Python 负责让失败可控、可追踪；所以 schema retry、timeout、tool error 和 fallback 都会进入 trace/eval，而不是在最终页面里被吞掉。
## STAR 3：用 Regression Eval 找到高精度但严重漏报

### Situation
早期系统看起来已经比较稳定，但没有 labelled corpus，无法回答“高风险信号到底漏了多少”。只看某次 alerts=0，甚至可能把过于保守误认为高质量。

### Task
建立能持续回归的产品级 Agent Eval，并让 mismatch 驱动通用规则修复，而不是写 happy-path demo。

### Action
构造 40 个项目特定 cases，覆盖 security/breaking dependency、routine release、`no breaking` / `without migration`、policy/tool/provider/schema、RSS/source noise、competitor/web change 和明确无关输入。第一轮 decision accuracy 75%、priority precision 100%、priority recall 28.57%。随后定位并修复 category multiplier 重复应用、mock stable context 漏接、关键词否定语义缺失，并给官方 vulnerability/supply-chain 增加 Python-owned alert floor。

### Result
当前 committed `resume-v1` suite 达到 40/40 decision、40/40 category、priority precision/recall 100%、FPR/FNR 0%，并通过 `--enforce` 接入 CI。这个结果只描述 SignalHarness contract corpus，不宣称通用模型能力。

### 30 秒回答
我后来发现 precision 100% 但 recall 只有 28.57%，说明系统不是很准，而是太保守。我用 40-case regression suite 把问题量化，再根据 mismatch 找到 scoring、context 和 negation 三个根因。修完后当前项目 contract suite 是 40/40，并变成 CI gate。
## STAR 4：把 Harness 接成 MCP / REST / Docker

### Situation
CLI 能证明 workflow，但 Agent 岗还会关注能力如何被其他 Agent、服务或应用复用。直接增加万能 MCP/file API 会绕过原有 permission guard。

### Task
增加真实可调用的接口与部署路径，同时保持单一 domain/runtime 事实源，不引入第二套权限系统、数据库或分布式队列。

### Action
用 MCP Python SDK 实现五个结构化只读 tools，只暴露 project context、signal history、assessment、trace 和 feedback；用 FastAPI 提供 run/trace/signals/feedback REST API，并挂载 MCP Streamable HTTP。每次 service run 隔离 output/state，所有扫描仍复用原 Workflow。Docker 运行同一个 `signal-harness serve` 入口并增加 `/health` healthcheck。

### Result
MCP 已通过官方 in-process 和 HTTP Client 的 `list_tools` / `call_tool`；REST 完整链路和路径穿越负例有集成测试；Docker 镜像实际 build/run，health status 为 healthy，容器内 mock-agent run 成功。

### 30 秒回答
我把 MCP 和 API 当成 Harness 的接口层，而不是重新做一套业务逻辑。MCP 只读，REST run 仍进入同一个 Workflow，权限和 learning gate 都不变。这样既能被外部 Agent 调用，也不会因为“接了 MCP”把安全边界绕开。

## 面试深挖原则

- 40-case suite 是 product regression corpus，不是学术 benchmark。
- provider contract eval 与 product decision eval 分开解释。
- 修复必须是 taxonomy/scoring/context 的通用规则，禁止按 event ID 特判。
- Docker/FastAPI 证明可运行接口，不等于宣称多租户、分布式或生产级横向扩展。
