# SignalHarness STAR 面试材料

## STAR 1：为什么做 SignalHarness

### Situation

开发团队每天面对 GitHub issues/releases、RSS、依赖更新、provider API 变化和安全博客。信息很多，但真正影响项目的变化很少。普通爬虫只能收集信息，普通 dashboard 只能展示信息，普通 chatbot 又缺少可审计边界。

### Task

做一个项目上下文驱动的 signal intelligence Agent Harness：它要能理解当前项目 profile，判断外部信息是否影响项目，并输出可追踪的 assessment、dashboard、trace 和 review-only learning proposal。

### Action

我实现了 watchlist、source collection、normalization、deduplication、noise filter、five-agent workflow、guarded scoring、trace 和 static dashboard。五个 Agent 分别负责 routing、evidence、impact、action planning 和 learning proposal；Python runtime 负责 schema validation、permission guard、tool allowlist、scoring、fallback 和 local outputs。

### Result

系统可以把外部变化转成 save/ignore/alert/action_required 决策，并生成可审计 dashboard。最新 live showcase 可以收集 12 条真实 signals，保留 save/ignore 的区分，同时不把普通外部信息都升级成 alerts。

### 面试时 30 秒回答

我做 SignalHarness 是因为真实团队的信息噪声很大。它不是聊天机器人，而是一个 Agent Harness：从 GitHub/RSS/Web change 收集信号，经五个 Agent 分工推理，再由 Python runtime 做 schema、permission、scoring 和 trace。最后输出本地 dashboard 和 review-only learning proposal，能解释每个判断是怎么来的。

### 面试官深挖问题

- 为什么不是直接写一个爬虫加 dashboard？
- 为什么需要五个 Agent，而不是一个 prompt？
- 这个项目怎么证明它是工程系统，不是 demo prompt？

### 推荐回答要点

- 爬虫只解决 collection，不解决 project impact reasoning。
- 五个 Agent 对应明确职责：route、evidence、impact、action、learning。
- Python runtime 控制所有外部副作用和最终 scoring，trace/dashboard 可审计。

## STAR 2：解决 fallback / schema 不稳定

### Situation

真实 OpenAI live run 曾经出现 schema invalid、fallback 或 retry。Agent 系统如果把这些隐藏起来，会让 dashboard 看起来漂亮但不可审计。

### Task

不能隐藏 fallback；要定位原因、降低 fallback，同时把真实 provider health、schema retry、tool errors 和 audit completion 清楚展示。

### Action

我做了 source-aware tool plan，减少模型请求无关工具；保留 schema retry 和 deterministic fallback；在 trace/dashboard 中展示 fallback health；把 skipped-stage audit completion 和真正 LLM fallback 分离，避免把 Supervisor skip 误解成模型失败。

### Result

v4 live run 中 fallback_count=0、schema_failures=0、tool_error_count=0。即使出现 source failure，也会在 Source health 里诚实展示，而不是隐藏。

### 面试时 30 秒回答

我没有把 fallback 当成要隐藏的错误，而是把它设计成 guardrail。真实模型可能 schema invalid、timeout 或 provider error，所以系统必须有 retry、fallback 和 trace。后来我把 audit completion 和真正 LLM fallback 分开，v4 live run 已经做到 fallback_count=0、schema_failures=0、tool_error_count=0。

### 面试官深挖问题

- fallback 是不是说明模型不可靠？
- 为什么不直接放宽 schema？
- fallback 输出会不会污染结果？

### 推荐回答要点

- fallback 是生产系统必须面对的不确定性边界。
- 放宽 schema 会降低可审计性；结构化失败应该显式暴露。
- fallback 输出只做保守 audit/default，不冒充完整 LLM reasoning。

## STAR 3：解决高分信号被依赖更新刷屏

### Situation

早期系统把 LangGraph 放在 dependencies，并且 dependency release / GitHub source 权重较高，导致很多普通 release 或 ecosystem issue 被推成高分信号。

### Task

让系统识别更广义的外部信息，而不是变成 dependency radar；同时 alerts 要高精度，不能把普通信息都变成告警。

### Action

我把 LangGraph 从 direct dependency 移到 monitored ecosystem；收紧 `dependency_update` 分类，要求 direct dependency 同时命中 breaking/security/schema/API/migration/regression 等影响词；调整 source/category scoring；收紧 alert policy；event limit 改为 source/category balanced，避免 GitHub issue/release 刷屏。

### Result

alerts 从泛化降到 0，high priority 不再被普通 release 刷屏。v4 live showcase 中 collected signals=12，save=3，ignore=9，alerts=0，action_required=0；普通外部信息被保留为 observed signals，而不是过度告警。

### 面试时 30 秒回答

我发现早期系统像 dependency radar，普通 release 太容易高分。后来我把 direct dependencies 和 monitored ecosystem 分开，收紧 dependency_update 的分类条件，并收紧 alert policy。结果 v4 live showcase 里 alerts=0，save=3，ignore=9，dashboard 更像一个信号筛选系统，而不是告警噪声制造器。

### 面试官深挖问题

- alerts=0 会不会太保守？
- 如何避免漏掉真正重要信号？
- 这个平衡怎么调？

### 推荐回答要点

- alerts=0 不代表没有价值；save/ignore 仍保留 full assessment。
- 真正高风险类别仍会通过 security/provider/schema/tool/eval/source-health 等路径升高。
- 可通过 `configs/signal_policy.yaml` 和 feedback/replay 做审查式调参。
