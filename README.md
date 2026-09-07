# SignalHarness

**中文** | [English](README.en.md)

> 一个面向持续开发软件项目的 Project Environment Intelligence 系统：连接项目后持续收集、聚合和分析周围的工程环境变化，回答“发生了什么、哪些值得关注、会影响什么、应该做什么”。Multi-Agent、规则、Search 与 Scoring 都只是可替换的实现技术。

SignalHarness 会实时监听 GitHub / RSS / 配置化网页快照等外部工程信号；它判断这些变化是否真正影响当前选择的项目，并把结果转成**可解释、可审计、可回归验证**的决策，而不是再做一个信息聚合器或聊天机器人。

## 30 秒看懂这个项目

普通爬虫擅长“收集”，普通 Dashboard 擅长“展示”，普通 LLM 擅长“推理”，但真实 Agent 系统还需要回答：

- 模型什么时候可以调用什么工具？
- 工具调用被拒绝或失败怎么办？
- 模型输出 JSON 不合法怎么办？
- 模型能不能直接决定最终业务分数？
- 一次判断为什么发生，之后能不能复盘？
- 修改 Prompt / Router / Scoring 后，如何防止旧能力被改坏？

SignalHarness 的核心答案是：**稳定的环境事实与 Scan 状态由 Python/runtime 持久化和约束，LLM 只负责真正需要语义判断的分析节点。** 当前五 Agent 路径保留为 Analyzer baseline，后续通过 Eval 决定哪些组件真正值得长期保留。

## 当前已实现能力

| 能力 | 当前实现 |
| --- | --- |
| Analyzer baseline | 当前五 Agent：Supervisor → Evidence → Impact → Action → Learning；P4 通过 Harness Ablation 决定最终形态 |
| Tool Calling | Evidence 两阶段工具计划；Python 负责 allowlist、permission、budget、execution、observation |
| 结构化输出 | Pydantic Schema、一次 schema retry、确定性 fallback |
| Guarded Scoring | LLM 提供语义判断；Python 持有 authoritative final score |
| Change Ledger | Project-scoped SQLite：EventRevision → Change → ProjectImpact → ScanChange；Top-K 不再决定事实是否存在 |
| Memory / State | Project-scoped Signal / Feedback / Learning 兼容状态；新业务历史逐步迁移到结构化 Ledger |
| Agent Eval | 40 条项目级 Regression Suite + 3 条 Cross-project Context Gate + Provider Contract Eval |
| Observability | Agent、Schema、Retry、Fallback、Tools、Latency、Tokens、Estimated Cost Trace |
| MCP | 5 个只读 structured tools |
| Service | FastAPI REST + SSE Streaming Run + MCP Streamable HTTP |
| Golden Demo | 默认中文，可切换 English，实时消费同一份 Trace |
| Deployment | Docker + `/health` healthcheck |
| Learning | Review-only proposal → replay/risk gate → 显式人工 apply |

## 总体架构

```mermaid
flowchart LR
    Sources[GitHub / RSS / Web change / fixture]
    Collect[Collect / Normalize / Deduplicate]
    Funnel[Project-aware Candidate Funnel]
    Noise[Noise Filter]
    Supervisor[SignalSupervisorAgent]
    Evidence[ContextEvidenceAgent]
    Tools[Controlled read-only tool loop]
    Impact[ImpactAnalystAgent]
    Action[ActionPlannerAgent]
    Learning[LearningPolicyAgent]
    Guard[Python constraint plane]
    Output[Assessment / Trace / Dashboard / Digest]
    Interfaces[CLI / REST / SSE Demo / MCP]

    Sources --> Collect --> Funnel --> Noise --> Supervisor --> Evidence --> Impact --> Action --> Learning --> Output
    Evidence --> Tools --> Evidence
    Guard -. schema / permission / budget / fallback / scoring .-> Supervisor
    Guard -.-> Evidence
    Guard -.-> Impact
    Guard -.-> Action
    Guard -.-> Learning
    Output --> Interfaces
```

核心边界：**Agent 负责语义推理；Python constraint plane 负责可验证规则和外部副作用。**

## Golden Demo：实时运行入口

启动服务：

```bash
uv run signal-harness serve \
  --host 127.0.0.1 \
  --port 8001
```

打开：

```text
http://127.0.0.1:8001/demo
```

页面默认是**中文**，右上角可切换 `EN`。运行前先选择**关联项目**，再选择数据来源、分析方式和真实模型。项目不是写死在 UI 中：`configs/projects/*.yaml` 是 Project Catalog，每个项目分别绑定自己的 Project Profile 与 Watchlist。公开仓库默认提供 `SignalHarness` 与 `Example · Agent API Service` 两个 profile，用来证明同一套 Workflow 可以按项目切换判断上下文。页面还可以选择本地项目目录生成 review-only onboarding draft：浏览器只读取白名单 manifest 与相对路径，不上传源码或 `.env`，也不会自动修改 Catalog。

默认组合是：

```text
SignalHarness + 实时 Watchlist + 离线五 Agent / mock-agent
```

它不需要 API Key，但仍然走真实五 Agent orchestration、Schema、Tool Guard、Trace、SSE 和 Python scoring，因此适合稳定展示真实 Harness 行为。

页面会实时展示：

```text
采集 + 标准化
      ↓
SignalSupervisorAgent
      ↓
ContextEvidenceAgent
      ↓
Python 工具守卫
      ↓
ImpactAnalystAgent
      ↓
ActionPlannerAgent
      ↓
LearningPolicyAgent
      ↓
受控决策
```

点击任一阶段可以查看：

- Schema 是否有效
- Retry / Fallback
- Tools requested / executed / blocked
- Permission checks
- Duration
- 最终 decision / score
- Live Trace
- Runtime health

### SSE 不是前端假动画

Golden Demo 的实时链路是：

```text
POST /stream-runs
        ↓
持久化 queued/running 输入与状态
        ↓
立即启动 SignalHarnessWorkflow
        ↓
浏览器可随时建立 EventSource
        ↓
TraceRecorder append / update
        ↓
SSE
        ↓
浏览器实时更新
```

`POST /stream-runs` 创建后就开始执行，不再依赖首个 SSE subscriber。queued/running 输入与状态会持久化，服务重启时可做有界恢复；浏览器断开不会取消任务，重连仍可通过 `Last-Event-ID` 补发当前进程内的历史事件。

这里仍明确不是 Redis/Celery/Kafka 一类分布式 durable queue：**任务输入/状态可恢复，SSE replay history 仍是 in-process。**

### Project Memory V2：Run 状态与项目长期状态分离

服务端每次 Run 仍保留独立 output / trace，但项目长期记忆按 `project_id` 持久化：

```text
.signal-harness/
├── projects/<project_id>/
│   ├── signal_memory.json
│   ├── feedback_memory.json
│   ├── alert_state.json
│   └── learning artifacts
└── service-runs/<run_id>/
```

同一项目的后续扫描会读取之前的 seen signals 与 feedback；不同项目彼此隔离。同项目并发 Run 在共享状态写入处串行化，避免覆盖同一个 JSON state。

### Project Onboarding V1：从真实项目生成待审核配置

`signal-harness project-draft <repo>` 会确定性读取 `pyproject.toml`、`package.json`、`requirements*.txt`、`Cargo.toml`、`go.mod` 和有限目录结构，生成 Project Profile + Watchlist 草案。默认只写 draft；只有显式 `--apply` 才注册到 Project Catalog，已存在目标时拒绝覆盖，除非明确 `--force`。

Golden Demo 的“导入本地项目草案”使用浏览器目录选择器，但只上传白名单 manifest 内容与相对路径列表；不会上传源代码、`.env` 或任意本机文件。`POST /project-drafts` 只返回 review-required 草案，不执行 apply，并限制单 manifest 256 KB、聚合 manifest 512 KB。

### Candidate Funnel V2：先判断相关性，再做 Top-K

实时 Watchlist 可能一次产生上千条原始事件。SignalHarness 不再先按时间把它们直接砍成 12 条，而是先完成 Normalize / Deduplicate，再用低成本的 Project Relevance、Focus Keyword、Source Authority、Recency 与 Novelty 做候选排序，并保留来源多样性后才进入五 Agent。

一次 2026-09-06 本地 live acceptance 中，Watchlist 从 1274 条 raw events 形成 12 条 project-aware candidates；这是运行证据，不是固定 benchmark。

### Source Authority V2

来源位置与“谁在说话”分开建模：GitHub Release 可视为 official；官方仓库里的普通用户 Issue 仍是 community；OWNER / MEMBER / COLLABORATOR Issue 作为 maintainer；Watchlist 中明确标记的 OpenAI/GitHub 官方 RSS 才是 official，独立专家 Feed 保持 secondary。Python 会 clamp LLM 报告的 source quality / confidence，模型不能把 community Issue 自行升级成 official。

### Change Delta V1：直接回答“这次变了什么”

标准化后的 Signal 会保留 source-native change metadata。GitHub Issue 使用 `created_at / updated_at` 区分新增与更新；GitHub Release 记录 `previous_version → current_version`；RSS 保留 published / updated 时间。统一时间窗口在 Normalize 之后执行，因此不同来源都按实际观察时间过滤，而不是让旧 RSS 混进“最近 14 天”。Golden Demo 会把这些 Delta 直接放在变化卡片顶部。

### Real Web Change V1：真实网页 baseline / snapshot diff

`web_changes.sources` 现在支持 `adapter: http` / `snapshot`。每个配置化公网 HTTP(S) 页面只做只读 GET，不执行页面 JavaScript；可见文本归一化后保存 project-scoped hash/snapshot。第一次观察只建立 baseline，页面未变化时输出 0 Signal，只有内容 hash 改变时才生成带 `Before / After` 摘要的 `web_change`。因此“首次看到页面”不会被伪装成“页面发生变化”，只有网页来源的项目也可以正常完成 0-change scan。

网络边界采用配置驱动的 allowlist：只允许 80/443 的公网 HTTP(S)，拒绝 credentials、localhost、`.local/.internal`、私网/loopback/link-local/metadata 等目标，每次 redirect 都重新校验，最多 3 次，响应正文有体积与文本 content-type 限制。更重要的是，Evidence Agent 的 `fetch_snapshot` **只能重读当前项目 Watchlist 已批准的 URL**，不能自行发明一个新网页把 `web_change` 变成通用 web fetch。Watchlist 标记 `official: true` 的网页由 Python provenance 判为 official，其他网页为 secondary。

### 不可信外部内容边界

GitHub/RSS/Web/ToolObservation 内容一律按 **untrusted external data** 处理。Prompt 明确禁止把来源正文中的“忽略之前指令、强制分类、调用工具”等文本当成 Agent 指令；确定性语义层也会剔除这类 instruction-like 句子后再做关键词分类和评分，同时保留原始正文用于证据审计。浏览器外链只允许 `http/https`，动态交互不把外部 event id 拼进 inline event handler。

## 三种运行模式

### 1. `mock-agent`：离线五 Agent 演示

```bash
uv run signal-harness scan \
  --fixture examples/signal_harness/sample_events.json \
  --mode mock-agent
```

使用 scripted offline provider，但走真实五 Agent 架构。公开 CI 和 Golden Demo 默认使用它。

### 2. `demo`：确定性基线

```bash
uv run signal-harness scan \
  --fixture examples/signal_harness/sample_events.json \
  --mode demo
```

这是 deterministic fallback，不应描述成真实多 Agent 执行。

### 3. `agent`：真实模型

```bash
LLM_API_KEY=... uv run signal-harness scan \
  --fixture examples/signal_harness/sample_events.json \
  --mode agent
```

`signal-harness serve` 启动时会自动读取项目根目录的 `.env`，但不会覆盖已经显式 export 的环境变量。Golden Demo 当前可识别并按 run 切换 OpenAI、Qwen、Kimi、DeepSeek 四套 OpenAI-compatible 配置；未配置的 provider 会保持不可选。Model Profile 带有 freshness metadata，过期的已知模型名会解析到当前 profile 并在 UI 明示 warning；未知自定义模型仍可配置，但 capability 会降为 conservative，不继承其他模型未经验证的 JSON/context/pricing 能力。

页面不会暴露 API Key、Base URL 或本地配置路径；“已配置”也只表示本地配置完整，真实网络连接仍在 Run 时验证。`.env` 已被 Git 和 Docker build context 排除。真实 `agent` 的交互关键路径在 ActionPlanner + guarded decision 后即可返回，LearningPolicyAgent 的 LLM reflection 后置到显式 calibration/learning 路径；`mock-agent` 仍保留完整五 Agent orchestration 作为稳定离线演示与回归路径。

## 五个 Agent 分别做什么

1. **SignalSupervisorAgent**
   对 Signal 分类并决定哪些下游阶段需要执行。

2. **ContextEvidenceAgent**
   先提出 bounded read-only tool requests；Python 校验并执行后，再根据 ToolObservation 合成证据和 confidence。

3. **ImpactAnalystAgent**
   结合 Project Profile 判断 semantic relevance、affected modules、conflicts 和 risk，但不能输出 authoritative final score。

4. **ActionPlannerAgent**
   生成 bounded、可逆、可审核的行动建议；高风险动作仍会被 Python 再次检查。

5. **LearningPolicyAgent**
   基于 Memory 提出 policy / watchlist / skill 的 review-only proposal，不自动修改配置。真实交互扫描默认将这一步后置，避免每次 Radar 扫描为 learning reflection 阻塞用户；显式 calibration/learning 流程仍调用同一个 Agent。

## 为什么最终分数不交给 LLM

SignalHarness 故意把最终业务判断留在 Python：

```text
Deterministic relevance
        +
LLM semantic relevance
        +
Evidence confidence
        +
Policy/category weights
        ↓
Python guarded score
        ↓
ignore / save / alert / action_required
```

这样做的目的不是“不相信模型”，而是把**可验证规则和不可完全验证的语义推理拆开**。

另外，官方来源中的 CVE / vulnerability / supply-chain 等高风险 Signal 可以触发 Python-owned priority floor，避免模型低估后静默漏报。

## Controlled Tool Calling

SignalHarness 不是让 Provider 原生自由调用工具，而是：

```text
LLM
 ↓
EvidenceToolPlan
 ↓
Python allowlist / permission / budget
 ↓
Tool execution
 ↓
ToolObservation
 ↓
LLM evidence synthesis
```

这意味着：

- 模型只能“提出工具请求”
- Python 决定是否执行
- Tool error / blocked / budget exceeded 都进入 Trace
- 工具失败不会被隐藏
- broad live search 默认没有开放

## 40 条 Agent Regression Eval

运行：

```bash
uv run signal-harness regression-eval \
  --mode mock-agent \
  --enforce
```

当前 committed `resume-v1` corpus：

```text
cases                  40 / 40 assessed
decision accuracy      1.0000
category accuracy      1.0000
priority precision     1.0000
priority recall        1.0000
false-positive rate    0.0000
false-negative rate    0.0000
```

覆盖包括：

- 高风险直接依赖变化
- 普通 release
- policy / permission / tool calling
- provider / structured-output 问题
- RSS expert signal 与采集噪声
- competitor / web change
- giveaway / crypto / gaming 等显式噪声
- `no breaking change` 一类否定语义

第一版 Regression baseline 曾出现：

```text
decision accuracy  75%
priority recall     28.57%
```

它真实暴露并推动修复了：

- category multiplier 重复计算
- mock provider 没拿到 stable project context
- 否定语义误判
- routing contract 回归

> **重要：这里的 100% 只代表 SignalHarness 项目级 Regression Contract，不代表通用 LLM 准确率 100%。**

## Cross-project Context Eval

多项目之后还要证明“Project Context 真的会改变判断”，而不是只换 Watchlist。CI 额外运行：

```bash
uv run signal-harness project-eval --enforce
```

当前 `project-context-v1` 为 3/3 PASS。同一事件分别在 `SignalHarness` 与 `Example Agent API Service` 下评分，要求目标项目的相关性得分有明确 gap；例如 MCP permission change 在 SignalHarness 中为 `SAVE`，在 Example Agent API Service 中为 `IGNORE`。

## Provider Contract Eval

Regression Eval 关注“产品行为有没有回归”；`model-eval` 关注“某个真实 Provider 能否稳定遵守 Harness contract”。

```bash
uv run signal-harness model-eval \
  --fixture examples/signal_harness/sample_events.json \
  --mode mock-agent \
  --runs 2
```

记录：

- schema-valid rate
- retry / fallback / timeout
- tool validation / blocked / runtime error
- bounded repair
- latency
- provider-reported tokens
- estimated cost（配置 pricing metadata 时）

历史真实 Provider 结果只作为 dated snapshot，不作为长期通用排行榜。详见 `docs/EVALS.md` 和 `docs/MODEL_EVAL_REPORT.md`。

## MCP

SignalHarness 暴露 5 个只读 Structured MCP Tools：

```text
signalharness_get_project_context
signalharness_search_signal_history
signalharness_get_latest_assessments
signalharness_get_run_trace
signalharness_get_feedback_memory
```

特点：

- read-only
- idempotent
- closed-world
- service run ID 校验
- 仍经过 SignalHarness permission policy

MCP 是第二个读取入口，不是绕过 Harness Guardrail 的后门。

## REST + SSE + MCP HTTP

启动：

```bash
uv run signal-harness serve --host 127.0.0.1 --port 8001
```

主要 endpoints：

```text
GET  /health
GET  /demo
GET  /demo/meta
POST /runs
GET  /runs/{run_id}
GET  /runs/{run_id}/trace
GET  /runs/{run_id}/assessments
GET  /runs/{run_id}/changes
GET  /runs/{run_id}/coverage
GET  /signals?run_id=...
POST /feedback
POST /stream-runs
GET  /stream-runs/{run_id}
GET  /stream-runs/{run_id}/events
```

MCP Streamable HTTP：

```text
/mcp
```

原 `POST /runs` 仍是同步 Run；stream-run 在 POST 后立即后台执行并具备本地有界重启恢复，SSE 只负责实时观察。两者复用同一个 `SignalHarnessWorkflow`。

## Observability

Trace 可以记录：

- Agent / model / mode / prompt version
- Schema validity
- Retry / Fallback / Timeout
- Tools requested / executed / blocked
- Permission checks
- Budget blocks / Tool errors
- Repair metadata
- Context hashes
- Duration
- Provider reported tokens
- Estimated USD cost

`mock-agent` 不伪造 token 数量。

CLI 查看：

```bash
uv run signal-harness trace
uv run signal-harness dashboard
```

Golden Demo 通过 SSE 消费**同一个 observable TraceRecorder**，不是维护另一套“看起来像 Agent 在跑”的前端状态机。

## Feedback 与 Guarded Learning

```bash
uv run signal-harness feedback \
  --signal-id demo-001 \
  --label useful \
  --note "checkpoint signals matter"

uv run signal-harness calibrate --mode mock-agent
uv run signal-harness learning-stage
uv run signal-harness learning-review
uv run signal-harness learning-apply --proposal-id <id> --yes
```

Learning proposal 不自动应用。高风险 proposal 或 replay gate failed proposal 保持 staged，需要显式 review / approval。

## Wheel / 仓库外运行

`uv build` 生成的 wheel 现在自带默认 `configs/`、Project Catalog、Model Profiles、Watchlists、Regression/Demo fixtures 与 Golden Demo 静态资源。运行时仍然**优先使用当前工作区自己的 `configs/`**；只有默认 `configs` / `examples/signal_harness` 在 cwd 不存在时，才回退到 package 内的只读资源。用户显式传入的其他路径不会被偷偷替换。

因此 wheel 安装后可以离开源码仓库直接运行，例如：

```bash
uv build
uv venv /tmp/signalharness-wheel
uv pip install --python /tmp/signalharness-wheel/bin/python dist/signalharness-0.1.0-py3-none-any.whl

cd /tmp
/tmp/signalharness-wheel/bin/signal-harness regression-eval --mode mock-agent --enforce
/tmp/signalharness-wheel/bin/signal-harness project-eval --enforce
```

Golden Demo 也不再把 40KB 级 HTML/CSS/JS 塞进 Python raw string：`demo.py` 只负责加载页面，实际资源位于 `ui/static/` 并通过 `/demo-assets/*` 提供。这样页面行为仍然保持 dependency-free，但静态资源可以被独立测试、缓存和维护。

## Docker

```bash
docker build -t signalharness:local .
docker run --rm -p 8001:8000 signalharness:local
```

浏览器打开：

```text
http://127.0.0.1:8001/demo
```

容器运行的是同一个 FastAPI + MCP + SSE service entry point，并提供 `/health` healthcheck。

## CI 与工程验证

公开 GitHub Actions 不依赖真实 LLM API：

```text
pytest tests/signal_harness
40-case mock-agent regression gate --enforce
3-case cross-project context gate --enforce
Ruff
mypy --strict
uv build
```

真实 Provider smoke 由本地显式环境变量触发，不把 API Key 放进 Public CI。`pytest` 同时覆盖 Golden Demo HTML/静态资源接线与仓库外默认资源 fallback；实际 Playwright 浏览器验收仍作为本地 release acceptance，避免 Public CI 为浏览器额外引入下载与环境依赖。

## 核心文件

```text
src/signal_harness/agent_team/          五 Agent roles
src/signal_harness/agent_integration/   prompts / runner / schemas / tool loop / trace
src/signal_harness/runtime/             workflow / permissions / registry / executor
src/signal_harness/signal/              scoring / candidate funnel / source authority / semantics
src/signal_harness/projects/            Project Catalog + onboarding + persistent project state
src/signal_harness/providers/           mock + OpenAI-compatible provider
src/signal_harness/mcp_server.py        只读 MCP interface
src/signal_harness/service.py           FastAPI REST/SSE + MCP HTTP
src/signal_harness/service_streaming.py stream-run / SSE replay manager
src/signal_harness/resources.py         repo-first / packaged-resource fallback
src/signal_harness/ui/demo.py           Golden Demo HTML loader
src/signal_harness/tools/web_snapshot.py safe public snapshot / visible-text diff
src/signal_harness/ui/static/           Golden Demo HTML / CSS / JS
src/signal_harness/evals.py             Regression + Provider Contract Eval
configs/projects/                       Project Catalog entries
configs/project_profiles/               Additional project profiles
configs/watchlists/                      Additional project-scoped Watchlists
configs/                                Default project / Policy / Model profiles
examples/signal_harness/                Demo / Regression fixtures
tests/signal_harness/                   Unit / Integration / Regression tests
```

## 项目边界

SignalHarness 当前**明确没有**：

- LangGraph / CrewAI / AutoGen orchestration dependency
- Redis / Celery / Kafka durable queue
- PostgreSQL / multi-tenant state
- VectorDB / Embedding / 通用 RAG
- Provider-native unrestricted tool calling
- 自动应用高风险 Learning proposal
- Production auth / horizontal scaling

这些不是通过 README 隐藏掉的“未来能力”，而是当前有意保持的 MVP 边界。

## 项目来源说明

SignalHarness 当前 package 不 vendor、也不 import OpenHarness runtime code；但仓库保留了早期探索阶段与 `HKUDS/OpenHarness` 的 upstream remote / common Git ancestry。

准确表述是：**借鉴现代 Agent Harness 模式，并围绕 Signal Intelligence 场景进行了实质性重构和独立实现。** 不声称项目从历史上完全没有 upstream 来源。

## 技术与项目文档

- `docs/ARCHITECTURE.md`
- `docs/EVALS.md`
- `docs/REPAIR_PASS.md`
- `docs/MODEL_EVAL_RESULTS.md`
- `docs/REAL_SOURCE_SMOKE.md`
- `docs/INTERVIEW_GUIDE.md`
- `docs/INTERVIEW_DEMO_SCRIPT.md`
- `docs/PROJECT_STAR.md`
- `docs/RESUME_GUIDE.md`

建议从 **Golden Demo → 40-case Eval → Trace/Tool Guard → MCP → 架构边界** 依次理解项目。
