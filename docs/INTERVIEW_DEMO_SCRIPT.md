# SignalHarness 面试展示讲稿

这份讲稿对应当前 `/demo` 与 `environment` 主链。旧 five-Agent Harness、scoring、MCP 和 Eval 仍是工程积累与回归链路，但不再作为产品第一叙事。

## 一句话

**SignalHarness 是一个面向持续开发项目的 Project Environment Intelligence 系统：它先理解项目，再持续收集项目周围的工程变化，把大量来源整理成与项目有关的 Change、Direction 和 Brief，并保留完整证据与执行 Trace。**

## 30 秒版本

真实项目每天会面对 GitHub Release/Issue、依赖版本、安全公告、Provider API、RSS、协议和网页变更。普通聚合器只会告诉我“发生了什么”，但我更想知道“哪些变化和这个项目有关、为什么、是否形成了方向、接下来要关注什么”。

SignalHarness 会先读取 Project Profile 和 Watchlist，再采集并冻结一个时间窗口内的来源事实。每个 Change 先走 cache / deterministic FactCapsule / bounded shallow model 三路解析，只有真正需要语义理解的变化才调用弱模型。最后强模型读取紧凑的完整环境 corpus，形成 Brief、Direction 和 Featured；Python 再负责 source independence、schema、历史状态、缓存、失败降级和持久化。Web、CLI 和历史报告读取的是同一份业务状态。

## 3 分钟版本

### 1. 为什么做

工程信息很多，但“新”不等于“对当前项目重要”。例如一个 OpenAI SDK Issue、Pydantic 版本、MCP 规范更新或者 GitHub 安全规则，只有结合项目当前依赖、Provider、协议和模块才能判断是否值得看。

所以 SignalHarness 的核心不是新闻摘要，而是：

```text
外部变化 + 当前项目上下文
        ↓
哪些真的相关？
        ↓
是否只是一个事件，还是多个独立变化形成了方向？
        ↓
为什么与项目有关？下一步看什么？
```

### 2. 主数据流

```text
Project Catalog
  ├─ Effective Project Profile
  └─ Project Watchlist
            ↓
GitHub / PyPI / OSV / RSS / Web / Local Git
            ↓
Collect → Normalize → Frozen Window → Revision Dedup
            ↓
Project-scoped Change Ledger
            ↓
Change Revisions
            ↓
cache ───────────────┐
deterministic capsule ├→ ChangeInsight
shallow model batch ──┘
            ↓
External corpus + Project activity context
            ↓
Compact DirectionDigest
            ↓
Strong-model global synthesis
            ↓
Python source/entity independence + semantic guards
            ↓
Brief + Directions + Featured + All Changes
            ↓
SQLite / History
            ↓
CLI / REST / SSE / React
```

关键设计是 **LLM 负责难以规则化的语义压缩与综合，Python 负责可以验证的事实、约束、身份、持久化和失败边界。**

### 3. 为什么不是每条都让大模型分析

成本和稳定性都不划算。当前 ChangeInsight 使用三路：

1. 命中相同项目上下文和策略的缓存就直接复用；
2. FactCapsule 已经能确定项目关系的简单变化走 deterministic path；
3. 只有语义缺口进入 bounded shallow batches。

然后强模型只做一次高层 synthesis，而且读取的是压缩后的 DirectionDigest，不重新吞完整网页正文和全部证据。

### 4. 为什么 Direction 不是模型说了算

最近真实测试里，模型会把“OpenAI 新闻 + openai-python release”误当成两个独立来源，从而把一次厂商发布包装成趋势。

现在 Python 会规范化：

- `OpenAI News` / `openai` / `openai/openai-python` → 同一 entity family；
- 同一 GitHub repo 的 Issue / Release → 同一 source channel；
- 默认 Direction 需要多个独立 entity family；同一 family 只有满足更严格的多 Change、多 channel 条件才允许保留。

所以单个重要发布仍可以是 **Featured Change**，但不会因为“重要”就被强行叫成 Direction。

### 5. Trace 为什么可信

扫描进度不是前端定时器。后端 `TraceRecorder` 在真实 workflow 执行时产生 `trace.step`，同一个模型调用完成后按相同 index 发 `trace.step.updated`。React 只消费 SSE 并展示白名单字段：阶段、Agent/模型、输入输出数、耗时、Token、缓存与 fallback。

因此面试时可以直接证明：页面上的执行过程与后端同一条数据链连接，而不是为了 Demo 写的假动画。

## 现场 Demo 推荐顺序

启动：

```bash
cd /Users/yu0/Workspace/10-Projects/SignalHarness
uv run signal-harness serve --host 127.0.0.1 --port 8001
```

打开：

```text
http://127.0.0.1:8001/demo
```

### 2–3 分钟稳定版

1. **选择 `SignalHarness` 项目**。先指出左侧 Project Context：不同项目有独立 Profile、Watchlist 和 Ledger，不是一个全局关键词列表。
2. **直接展示已保存报告**，不现场依赖 Provider。当前 2026-09-11 保存快照来自真实来源：1533 observations → 307 Changes，其中 228 relevant，3 Featured，5 Directions。这个数字是一次 dated product snapshot，不是 benchmark。
3. **展开一个 Direction**。先讲 explanation / project connection / watch next，再点击“查看支持与反向证据”，说明 Direction 能回到具体 Change，而不是不可审计的总结。
4. **看 Featured**。说明“单个很重要的事件”和“跨变化形成的 Direction”被刻意分开。
5. **切到全部变化**。展示 228 条相关变化仍然在，不是只把 Top-K 喂给模型后把其他事实丢掉。
6. **最后看技术审计 / Trace**。说明真实扫描时这里通过 SSE 实时出现；每个模型调用先 running、再原 index update，能看到 latency/token/cache/fallback，但不展示隐藏 chain-of-thought。
7. **终端补一个无模型读取**：

```bash
uv run signal-harness environment-report --project signalharness
```

强调 CLI 与 Web 读的是同一个持久化 EnvironmentReport。

### 如果面试官要求现场跑

可以点击“检查最新变化”，但要提前说明真实来源和强模型 synthesis 可能需要分钟级时间；这是当前 MVP 的性能债，不应该拿现场网络和 Provider 状态赌演示成功。

更稳的表达是：

> 我保留实时入口证明链路可以跑，但面试主 Demo 默认读取最近一次真实完整 Scan，因为环境情报产品本来就应该把历史报告持久化，而不是刷新页面就重新烧模型。

## 当前保存 Demo 的真实状态

2026-09-11 的正式 `signalharness` 项目保存报告：

- 1533 observations
- 307 Changes
- 279 external environment Changes
- 28 project activity Changes
- 228 relevant
- 3 Featured
- 5 Directions
- 0 automatic Deep Dive
- report status: complete
- coverage: unknown

另外，2026-09-11 新做过一轮独立 48h fresh quality slice：141 Changes。该轮暴露并修复了 shallow copy、Kimi K3 provider contract、Direction source-independence 等真实问题。它用于工程验收，不需要为了面试再重复调用 API。

## 和常见项目相比怎么讲

### 和新闻聚合 / RSS Reader

它们擅长收集和分类；SignalHarness 多了一层 **project-relative interpretation**：同一个变化在不同项目里可以有不同相关性，显式 Preference 也会进入 effective Profile。

### 和普通 RAG

RAG 通常是“用户提问 → 检索 → 回答”。SignalHarness 是周期性的 **environment scan**：先冻结一个完整时间窗口，把 Change 和 evidence 持久化，再形成 Direction，并支持历史连续观察。

### 和 LangGraph / CrewAI

这些是通用 Agent orchestration framework。SignalHarness 当前的特色不是“自己重写一个更大的框架”，而是展示我对生产边界的理解：

- source collection 与模型推理解耦；
- Python owns frozen window / identity / permission / persistence；
- cheap shallow → expensive synthesis 的分层成本结构；
- evidence-backed Direction；
- real SSE Trace；
- degraded report / provider fallback；
- CLI/Web 共用一条 workflow。

旧 five-Agent Harness 仍保留，说明项目曾深入做过 routing、tool guard、scoring、repair、eval；但当前产品主线已经收敛，不为了“Agent 数量多”继续复杂化。

## 项目特色，面试时重点讲 4 个

1. **Project-aware，不是 generic feed**：Profile/依赖/Provider/协议/Preference 真正进入 ChangeInsight 和 synthesis。
2. **Full-corpus first**：所有 Change 先持久化并浅解释，再形成 Direction；Featured 只改变阅读顺序，不决定事实是否存在。
3. **Direction 有确定性独立性约束**：防止单厂商/单仓库自己制造“趋势”。
4. **真实可观察链路**：TraceRecorder → SSE → React；CLI/REST/Web 读同一持久状态，错误可以 degraded 而不是假装成功。

## 当前已知债务，要主动讲清楚

这些不是现在要继续重构的理由，但面试官问“如果上线还要做什么”时可以回答：

- 141 Change 的强模型 synthesis 目前约 140–180 秒，下一阶段会压缩输入、优化 reasoning effort / provider policy，必要时再评估 hierarchical synthesis；
- 一次采集可能拿到很多最终落在窗口外的 raw observations，source-side checkpoint / ETag / `since` 仍可优化；
- coverage 对部分来源只能标 `unknown`，不会冒充完整互联网覆盖；
- Direction/Brief 目前有真实样本审计，但没有足够人工标注形成正式 quality benchmark；
- 当前是单机 SQLite + 本地 StreamRunManager，不冒充 Redis/Celery/Temporal 级分布式任务系统；
- 生产级 auth、multi-tenant、quota/budget 和外部 notification delivery 仍属于下一阶段。

这些债务不推翻当前架构：Sources、Project State、Change Ledger、Model Layer、Persistence 和 Interface 已经分层，可以逐步替换。

## 常见追问

### 为什么不用一个强模型直接读全部网页？

因为成本、上下文体积、可重复性和证据审计都会变差。来源事实先由 Python 冻结和结构化，大部分 Change 用 cache/deterministic/shallow 处理，强模型只看 compact corpus 做真正需要全局视角的 synthesis。

### 为什么 Direction 还需要 Python guard？

模型很擅长归纳，但不适合成为“来源是否独立”的最终裁判。同一厂商博客、SDK repo 和 Issue 看起来是三条链接，本质可能是一个主体。身份归一和最小独立证据要求属于确定性约束。

### 为什么不用向量数据库？

当前 MVP 的核心规模和访问模式由 frozen window + SQLite Change Ledger 足够覆盖。向量检索只有在长历史跨窗口检索成为真实瓶颈后再引入，否则只是额外复杂度。

### 为什么不继续五 Agent？

项目早期用多 Agent 拆责任来验证 Tool Guard、schema、fallback 和 eval 边界。产品收敛后发现大量步骤可以由 deterministic runtime 和两层模型更简单地完成，所以当前主链故意减少 orchestration，而不是为了展示 Agent 概念保留复杂度。

### 项目最难的地方是什么？

不是接一个 LLM API，而是定义哪些东西应该由模型决定、哪些必须由 runtime 决定。例如 Change identity、时间窗口、source independence、权限、缓存和历史状态都不能靠 Prompt 保证；而“这个变化为什么和项目有关”“多个变化是否共同形成一个方向”更适合模型。SignalHarness 的主要工程价值就在这条边界。

## 最后一句

> 我没有把 SignalHarness 做成一个追求 Agent 数量的 Demo，而是把它收敛成一个真实可跑的项目环境情报闭环：来源事实可追溯、项目上下文真的参与判断、模型只做需要语义理解的部分、结果能持久化、前端能实时看到真实执行过程，失败也有明确降级边界。
