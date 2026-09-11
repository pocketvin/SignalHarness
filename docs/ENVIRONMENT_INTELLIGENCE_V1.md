# 方向优先的环境情报：首个可运行闭环

更新日期：2026-09-11。当前实现范围：`environment-v1.6`。这是第一轮工程闭环，不代表完整产品质量验收已经结束。

## 正常产品流程

```text
共享来源采集（GitHub / Local Git / PyPI / OSV / RSS / Web）
→ Event 身份与内容修订
→ 确定性跨来源 Change 聚合 + 冻结 ChangeRevision / 证据
→ 每个 Change 构造 FactCapsule，先查可验证缓存
→ 能确定性完成的结构化 Change 直接生成 ChangeInsight（0 token）
→ 只有语义项进入弱模型队列（默认每批最多 12 条、并发 3、失败拆批）
→ 每个 Change 最终都有 ChangeInsight
→ DirectionDigestBuilder 将每条 Insight 压成全局综合专用紧凑行
→ CorpusOrganizer 保留全部 Change，仅增加确定性索引/排序
→ 一次 EnvironmentSynthesizer 调用
   输出方向、整体报告和最多 5 条 Featured
→ 保存历史报告，按需 SQL 查询相关/全部变化
```

正常扫描不运行旧的 Top-12 Evidence/Impact/Action 路径，不生成任何 Deep Dive job。Featured 只是阅读顺序。模型返回缺项、引用错误、非中文正文时做有限修复；仍失败则显示解释缺失/报告降级，不把规则文案冒充模型理解。

点击具体变化时：

```text
显式 POST
→ 读取冻结的 Change / Profile 版本
→ 读取已授权本地项目的有限引用（若有）
→ 幂等预约 Deep Dive job
→ 有界重新核对原始 URL
→ 强模型分析影响与可逆验证建议
→ 保存独立结果，按需复用
```

GET、方向证据列表、悬停均不启动模型。Deep Dive 不改写历史报告，不执行项目代码，不创建 PR。来源重新抓取后的内容拥有独立引用 ID，不伪装成原来的冻结快照。

## 模块职责

| 文件/目录 | 唯一职责 |
| --- | --- |
| `runtime/workflow.py` | 共享采集入口；新产品分支在旧候选筛选之前切出；旧链路保留回归对照 |
| `runtime/environment_scan.py` | 新扫描的领域冻结、分析调用、产物和完成状态 |
| `intelligence/contracts.py` | 产品契约；不把评分/模型选择变成产品字段 |
| `intelligence/corpus.py` | 确定性聚合、引用/偏好/方向状态约束 |
| `intelligence/fact_capsule.py` | 从 ChangeRevision 提取模型前可确定的事实与精确项目关系 |
| `intelligence/project_projection.py` | 将紧凑 project refs 与模型短 note 收敛成 Python-owned relation/basis 和稳定人类可读说明 |
| `intelligence/semantic_router.py` | cache / deterministic / semantic 的确定性路由与 Tiny 候选判断 |
| `intelligence/batch_planner.py` | 语义项的多样性平衡 Batch、大小预算与可复现顺序 |
| `intelligence/direction_digest.py` | 将完整 ChangeInsight 压成强模型使用的全量紧凑 DirectionDigest |
| `intelligence/engine.py` | 三路 Insight 解析、有界并行弱模型 + 单次全局综合；缓存与可见降级 |
| `intelligence/model_calls.py` | 有界调用、中文/结构校验、重试、备用模型与真实审计 |
| `providers/task_policy.py` | 服务端按任务选模型，使用各自凭据命名空间 |
| `persistence/intelligence.py` | 同一 Ledger 中的版本化表与 SQL 筛选/分页 |
| `intelligence/deep_dive.py` | 点击触发、幂等、并发上限、证据核对与独立任务状态 |
| `intelligence/usage.py` | 授权源码中的有界文本/导入引用；不是可达性证明 |
| `service_intelligence.py` | 不接受模型、mock、fixture 或 Top-K 参数的产品 API |
| `frontend/src/environment/` | 方向、报告、相关变化、点击核实、项目关注与定期检查 |
| `cli_environment.py` | 与 Web 相同的新产品流程的 CLI 薄入口 |

`capability_eval` 使用的共享输出 Schema 已移到 `agent_integration/schemas.py`。Mock Provider 不再反向 import `capability_eval`，打断原来的 production → eval → workflow 依赖环。

## 入口

Web 仍使用 `/demo` 路由，页面已是新产品工作区，不再是可选择 mock 的演示面板。

```bash
cd /Users/yu0/Workspace/10-Projects/SignalHarness
uv run signal-harness serve --host 127.0.0.1 --port 8001

# 真正采集 + 新环境分析（会调用已配置的真实接口）
uv run signal-harness environment --project signalharness --window since_last

# 只读，绝不调用模型
uv run signal-harness environment-report --project signalharness
```

已有服务占用端口时不要盲目启动或结束其他进程。默认 CLI `scan` 与旧 MCP 工具仍是兼容链路，不能把它们误称为已迁移的新环境分析入口。

产品 API 主干：

```text
GET  /intelligence/meta
GET  /intelligence/projects/{project_id}
POST /intelligence/projects/{project_id}/scans
GET  /intelligence/projects/{project_id}/reports/{scan_id}
GET  /intelligence/projects/{project_id}/reports/{scan_id}/changes
POST /intelligence/projects/{project_id}/reports/{scan_id}/changes/{change_id}/deep-dive
GET  /intelligence/projects/{project_id}/deep-dives/{job_id}/events
GET  /intelligence/projects/{project_id}/directions/{direction_id}/history
GET/POST/DELETE /intelligence/projects/{project_id}/schedules[/schedule_id]
```

扫描使用已有 SSE 承载 `product.progress`；深挖有独立 `deep.updated`。进度来自真实阶段完成，不播放伪造思考文本或百分比。普通页面不呈现原始 Trace 控制台；审计信息在折叠区。

## 服务端模型策略

`configs/intelligence_policy.yaml` 是本产品的任务级配置，前端没有选择入口。

- 浅分析：Qwen Plus 优先，非思考结构化调用；Kimi 为有界备用。
- 全局综合、深度核实：本轮实测可用的 DeepSeek V4 Pro 优先，Kimi K3 备用。
- 显式模型 pin 避免旧 `DEEPSEEK_MODEL=deepseek-v4-flash` 环境覆盖把新任务悄悄退回旧型号；凭据和 endpoint 不迁移、不输出。
- 单调用有超时、输出上限、一次结构修复；单任务最多尝试两个配置可用的 Provider。配置可用不等于已通过在线健康验证。
- 固定模型名不保证服务商永远提供同一权重；更新策略必须核对官方文档并重新做小样本验收。

2026-09-11 查询的 DeepSeek 官方价格/模型页面提示：2026-09-14 北京时间 12:00 后，V4 Pro 请求会被路由到 V4.1 Flash。这是后端策略维护事项，不应重新加回前端选择器。来源：https://api-docs.deepseek.com/quick_start/pricing/

## 版本、真实性和权限边界

SQLite schema 升级为 v8，新增 ChangeRevision / 证据关联、ScanInsight、InsightCache、EnvironmentReport、DirectionRevision、DeepDiveJob。不是新增第二种数据库。

- 已发布报告与其 ProfileRevision 不因后续偏好、来源更新或 Deep Dive 被覆盖。
- 已提交报告恢复时直接读取快照，不重新采集/调用模型。未提交的中断扫描仍是有界整轮重试，不是任意节点精确恢复。
- Deep Dive 缓存绑定项目、ChangeRevision、ProfileRevision、分析版本、任务模型策略、源码检查指纹和证据刷新时间桶。
- 首次观察不会直接标为“加速”。比较增强/减弱要求可比、非重叠时间窗口和相同来源集合；部分覆盖或反证会显示有待核实。
- 引用 ID 存在和中文格式正确，不等于所有语义推论都已被独立证实。方向质量仍需真实语料评测。
- 本地源码读取只限已连接 Local Git roots，跳过隐藏/私密目录、符号链接、依赖/构建目录与疑似凭据文本。浏览器依赖清单上传并不授权后端读取整份本地目录。
- 服务目前按单用户、单进程、本机运行设计，不是开放公网多租户服务。

## 本轮验收证据

自动测试覆盖：300 条全量输入、批次完整性、跨源 release 合并、引用约束、中文正文约束、无自动深挖、SQL 分页/搜索、Profile 缓存失效、历史不可改写、GET 不调用、重复 POST 复用任务、项目隔离、定时产品模式、源码隐私边界。

300 条测试为脚本 Provider 的结构/数据流验收：默认 15 批浅理解 + 1 次全局综合；不是 300 条真实语料的模型准确率评估。

手动真实接口验收使用之前保存的 8 条来源观察，整理为 6 个 Change，Qwen Plus 完成浅分析，DeepSeek V4 Pro 完成中文全局综合，形成 3 个 Direction，自动 Deep Dive 为 0。它是资料回放，不是重新采集本期完整外部环境。第一次 Kimi 请求失败，且第一次综合返回英文；中文校验修复后才取得上述通过结果。不能把 Kimi 配置存在写成其在线验收通过。

最终离线回归 **409 tests passed**；Ruff、mypy（132 个源文件）、前端 typecheck/build、Python wheel/sdist 打包和隔离的旧 scan/trace/calibrate 均通过。浏览器验收涵盖 1440px 桌面、390px 手机、方向证据导航、真实缓存核实面板、Escape 关闭和搜索空结果，无 pageerror 和手机横向溢出。

另完成一次真实 Provider 的点击核实：任务与缓存链路完成，历史报告保持不变；但两个远端原始页面复查均失败，结果明确保留“仅有已保存证据、无法确认源码 diff”的不确定性。本地 217 个源文件的有界检查返回 24 条文本/导入引用，不能将这些引用数量当作 24 个受影响调用点。

结果目录：`outputs/p0-environment-20260911/`。隔离验收状态、日志与截图在 `work/p0-environment-20260911/`；没有将回放结果写入用户正式 `.signal-harness`。

## 2026-09-11 P0 语义质量收口（environment-v1.5）

这一轮不是增加 Agent，而是把“什么可以成为环境方向证据”变成可测试的产品边界。

### 外部环境与项目自身活动分离

所有 Change 仍然做浅层理解，但新增 `corpus_role`：

- `external_environment`：可进入环境 Brief、Direction 和 Featured；
- `project_activity`：只用于说明“为什么这个外部方向与当前项目有关”，不能反过来证明外部环境正在形成趋势。

真实历史来源回放中，71 条已保存观察确定性整理为 67 个 Change，其中 63 个是外部环境变化、4 个是项目自身活动。67 条全部完成浅层解释，48 条进入相关变化投影。项目自身 4 条在 Web 中有单独入口，没有混入“全部环境变化”。

### Direction 质量门

`intelligence/quality.py` 只拦截可以从冻结数据确定为不合法的输出，不尝试替模型判断趋势真伪：

- Direction 只能引用外部环境 Change；
- 默认至少跨两个实体；如果只围绕同一实体，则至少 3 个 Change 且来自 2 个来源；
- 高度共享同一证据集、且文本主题相近的 Direction 必须合并；
- “同日/当天”必须与全部引用日期一致；“短时间内/短期内”必须有完整日期且跨度不超过 7 天；
- “加速/增强/减弱/同比/环比”等跨期状态不能由单次模型直接写入 Direction 标题/解释，状态由可比历史窗口确定性计算；
- 项目文案禁止暴露 `critical_modules`、`dependencies`、`chg-...` 等内部字段/标识；
- GitHub Issue 只代表报告、提议或讨论，不等于缺陷已确认、功能已发布或修复已落地。

Direction 还增加确定性 `evidence_posture`：`reported_issue / mixed / observed_change`。Web 对应显示“问题 / 讨论信号”“混合证据”“已观察变化”。同时显示支持变化数与来源数；`authoritative_source_count` 单独保存，来源权威性由已有 `event_source_quality()` 计算，而不是让模型自报。

### 输出更少、职责更清楚

V1 删除了强模型输出 Schema 中独立的 `risks` / `opportunities` 字段。风险、机会和下一步观察统一收进对应 Direction 的 `watch_next`；`EnvironmentReport` 仍保留空数组字段以兼容旧读者。这样减少第二套自由发挥的结论，也避免和 Direction 重复。

浅层和全局综合的版本身份已经分开。当前 shallow provider/cache identity 已进入 `environment-shallow-v1.7`；经过现行 Schema/语言/证据约束验证的 `environment-shallow-v1.6` 与旧 `environment-v1.3` cache 仍可迁移复用。报告/综合版本单独管理，因此修改 Direction 质量规则时不必重新支付浅层模型调用。当前浅层默认 12 条/批；大批失败会确定性二分后局部重试，仍不会退回 Top-K。

### 真实保存来源回放结果

本轮使用过去真实采集并保存在本地的 71 条来源观察进行隔离回放；它不是 2026-09-11 当下的重新采集，因此不能用来证明当前外部世界仍完全相同。

最终 `environment-v1.5`：

```text
71 source observations
→ 67 Change
→ 63 external + 4 project activity
→ 67 / 67 shallow ready
→ 48 relevant
→ ONE global synthesis over all 63 external ChangeInsights
→ 5 Directions + 5 Featured
→ 0 automatic Deep Dive
```

中间失败同样保留为验收证据，而不是只记录最终通过：

- v1.3：全局综合未通过旧质量门，保存 degraded 报告；
- v1.4：DeepSeek 两次被 `direction_source_diversity` 拒绝，说明同一仓库/同一实体的 Issue 聚集仍被模型误写为环境方向；没有放宽 guard，而是增加定向修复提示；
- v1.5：DeepSeek 第一次被 `temporal_window_mismatch` 拒绝，第二次按明确失败类型修复后通过；成功综合调用约 46,838 provider tokens、约 144 秒；
- Kimi K3 在此前备用实测仍出现 provider-level 失败，不能写成“已稳定通过”。

最终 5 个 Direction 的证据集合两两重叠率为 0；没有项目自身 Change 支撑 Direction/Brief，没有内部字段或 Change ID 泄漏，没有已知错误“同日”断言。该结果是**结构化语义质量门通过**，不是对所有结论真实性的独立人工/网页复核。

浏览器验收覆盖方向证据姿态、外部/项目自身分区、方向证据导航和移动端。主页面不再展示独立风险/机会块，也没有模型选择、mock、影响分或 raw Change ID。

## 2026-09-11 浅层 Project Projection 收敛（environment-shallow-v1.7）

上一轮 fresh cost observation 证明 shallow 占模型 token 的 60.6%，其中 `relation_reason` 平均约 127 字且 46/46 都重复出现项目名。当前实现不再让弱模型自由复述整份 Project Profile：

```text
FactCapsule
→ immutable source/entity aliases + Project Profile
→ deterministic relation/basis when known
→ compact project refs
   d = dependency
   p = protocol
   r = runtime
   v = provider
   m = module/capability
   e = monitored ecosystem
→ ShallowModelBatch
   id/s/f/r/b/n/a/t/e/u
→ Python per-item projection resolver
→ stable ShallowInsight / ChangeInsight
```

模型只需要为项目关系提供一个短 note；已知 direct/context 与 basis 由 Python/FactCapsule 固定。`direct` 不能由 module/ecosystem 单独建立；模型若选择一个具体依赖作为 context，该依据必须真实出现在当前 Change，否则逐条降级。**relation/basis 的局部错误不会触发整个 Batch repair**；Schema、证据 ID、中文和内部字段泄漏仍属于真正需要模型修复的输出错误。

持久化 ChangeRevision 身份没有为此修改：例如 `pydantic/pydantic` 仍是原始 GitHub source/entity；FactCapsule 只从 immutable evidence source path 派生匹配 alias：具体 direct 只使用较保守的 Change entity / repo basename，GitHub owner 片段最多帮助 ecosystem context，不能单独制造 direct dependency。这样既能得到 `pydantic` 等合理关联，也避免同一 revision ID 因成本优化出现两套 digest 内容。

冻结 SignalHarness Project Profile 的 wire 体积：Project Context ~950 → ~700 bytes（73.7%）；provider wire Schema ~1,241 → ~1,067 bytes（86%）。这是 JSON/UTF-8 bytes，不是 token 等价比例。

两次 8-anchor Qwen 探针都保留：

- **v17**：1 attempt，3,820 prompt + 1,226 completion = 5,046 tokens；relation 文案平均 ~135.9 → ~31.9 字；completion ~153 token/anchor，对比旧 fresh run ~233 token/semantic Change。它证明短 wire 有成本潜力，但 relation 等级/basis 仍有模型漂移。
- **v18 strict-guard experiment**：首轮 relation/basis 被 guard 拒绝，整 Batch 重写；2 attempts 合计 10,862 tokens。这个结果证明“一个 projection 错误 → 整 Batch repair”会抵消节省，因此没有采纳。
- **最终 resolver**：没有第三次 API。直接重放已经付费的 v18 输出，7/8 anchor 可从 Project Profile/source identity 得到确定 relation/basis；8/8 符合当前工程 policy。这里是 policy consistency，不是人工事实准确率。

`ShallowModelBatch` 的短字段 `s/f/n/u` 已显式纳入原有中文与内部字段泄漏 guard，wire 变短不会绕过产品文案质量门。

本 checkpoint 两次 8-anchor Qwen 探针合计消耗 **15,908 provider-reported tokens**（v17 5,046；v18 10,862）；没有调用 DeepSeek，也没有第三次真实探针。最终冻结源码验收为 **444 Python tests passed**、Ruff PASS、mypy PASS（138 source files）、TypeScript/Vite PASS、wheel/sdist PASS、import-cycle SCC=0；旧 mock scan/trace/calibrate 兼容链也通过。

## 2026-09-11 真实成本观察（受控 12h fresh scan）

这一轮先执行 collect-only preflight，不调用模型。24h 窗口得到 96 Change，其中 88 条需要 semantic、8 个弱 Batch、约 106 KB semantic 输入，超过实验闸门，因此**没有启动 24h 的付费模型扫描**。缩到固定 12h 后为 53 Change：7 deterministic + 46 semantic、4 个弱 Batch、约 55.7 KB semantic 输入，才允许继续。

实际模型 run 使用隔离 state、`interactive=false`、固定 custom window；Qwen 只负责 shallow，DeepSeek V4 Pro 只负责 synthesis，关闭本轮 Kimi fallback。最终 53/53 Insight ready，46 external + 7 project activity，32 Relevant、3 Featured、4 Direction、0 auto Deep Dive；没有 batch split、repair 或 fallback。

Provider 返回的真实 usage：

```text
Qwen shallow       4 attempts
prompt             18,509
completion         10,709
total              29,218

DeepSeek synthesis 1 attempt
prompt             10,423
completion          8,585
total              19,008

All model attempts
prompt             28,932
completion         19,294
total              48,226 tokens
```

这次 shallow 占总 token 的约 **60.6%**。46 条 semantic Change 平均每条分摊约 635 provider-reported tokens（包含共享 Prompt/Schema/Project Context 的 batch 开销），因此后续若继续省 token，应优先减少 shallow 的重复输入/输出，而不是继续压已经很小的 Global Digest。

全局侧：完整 ProductChange 序列化约 133,537 bytes；DirectionDigest corpus 约 17,388 bytes（约 13.0%）；带 Project / source / manifest 等完整 strong request 约 28,925 bytes。说明“强模型看完整环境”当前没有依赖 Top-K，也没有再次吞完整 Evidence。

成本审计现在会统计成功调用以及**已经返回但被结构/语义 guard 拒绝**的 provider usage；如果 provider 异常前 SDK 已拿到 usage，也尽可能记录。Qwen/DeepSeek 当前 model profile 没有单价字段，`usage_source=provider_reported_no_pricing`，因此 USD 成本是**未知**，不能把 `estimated_cost_usd=0` 理解为免费。

本次浅层 46 条的平均文案长度约为：summary 51 字、what_changed 113 字、relation_reason 127 字；后者最明显重复 Project Profile。离线截断模拟显示把 summary/what_changed/relation_reason 分别约束在 60/120/80 字并限制 3 个 topics，可将这些用户文本字符数减少约 18.4%，但这只是**零 API 容量估算**，尚未证明模型按新契约生成时语义质量不下降，所以本轮不改 shallow Prompt/Schema、不再跑第二次真实模型。

来源侧另发现 OpenAI News RSS 一次返回 1,189 个 raw item；冻结 12h 窗口最终只保留整体 60 条 observation，因此它没有直接放大模型 token，但会浪费采集带宽/解析成本。RSS/Web 当前 coverage 保守为 `unknown`，所以本次 Direction state 均为 `uncertain`；这是来源覆盖语义，不应该靠模型改写成“确定趋势”。

## 2026-09-11 P0 成本感知主链（environment-v1.6）

新的产品不变量是：**全量理解不等于全量调用模型。每个 Change 必须得到 ChangeInsight，但 Insight 的来源可以是 cache、deterministic 或 semantic。**

```text
ChangeRevision
→ FactCapsule
→ validated cache ?
   ├─ hit → ChangeInsight
   └─ miss
      → deterministic eligible ?
         ├─ yes → ChangeInsight (0 model token)
         └─ no  → BalancedBatchPlanner → bounded parallel ChangeInterpreter
→ all ChangeInsights
→ DirectionDigestBuilder
→ CorpusOrganizer
→ full compact corpus budget check
→ ONE EnvironmentSynthesizer
```

当前 deterministic bypass 故意保守：正常 PyPI/package-registry 发布且能精确关联项目、以及项目自身活动可以跳过弱模型；GitHub Release、安全公告、Issue、RSS、Web diff 或关系不明确的 Change 继续走语义理解。Package metadata 出现非零 yanked 时也强制回到 semantic；如果同一个 package release 已聚合 GitHub Release 等更丰富来源，也继续走 semantic，避免 0-token 路径丢掉 release notes。

弱模型默认 `batch_size=12`、`shallow_concurrency=3`。BatchPlanner 在能做到时按实体 round-robin，降低同一实体密集内容造成的 priming；调用完成顺序不影响最终顺序，结果按 Change identity 重新收敛。失败 Batch 可以二分重试，但每次真实调用仍受同一个 Semaphore 限制。并行状态下成功调用 receipt 使用 task-local ContextVar，避免另一个 Batch 先完成后污染缓存 provenance。

强模型不再接收完整 `ProductChange`。每条 `DirectionDigest` 只保留短字段：Change ID、实体、类型、天级日期、证据姿态、≤88 字事实、最多 3 个主题、项目关系和最多 3 个来源 identity；字段图例只发送一次。完整 Evidence、长说明和 Deep Dive 数据仍在持久层。当前 `global_input_bytes=450000`，它是保守 UTF-8 transport budget，不冒充精确 token 计数。

零 API 离线验收：

- 1,000 个结构化 package release → 1,000 deterministic Insight，**0 weak call + 1 scripted strong call**，完整 1,000 个 Digest 均进入全局综合；完整 ProductChange 序列化约 966 KB，DirectionDigest 约 245 KB（25.3%），全局请求约 257 KB，低于 450 KB budget。
- 1,000 个全部需要语义理解的 scripted Change → 84 个弱 Batch + 1 个强综合，完整 1,000 条仍进入 Global Corpus，没有 Top-K。
- 24 条混合场景第一次为 12 deterministic + 12 semantic，只需 1 个弱 Batch；相同 Revision 第二次为 12 deterministic + 12 cache，弱模型调用为 0。
- 对上一轮 71 条真实保存来源观察做 **0 API 静态路由估算**：67 Change 中，即使完全不使用缓存，也有 8 条可以保守 deterministic、59 条需要 semantic，规划为 5 个 Batch（并发上限 3）；这些 semantic Change 的模型输入从旧版约 100,935 bytes 降为约 64,034 bytes，即 63.4%。这是字节口径，不冒充精确 token 数。
- Tiny fast path 已有 whole-corpus 判断点（≤4 条且输入≤24 KB），**尚未激活**。只有整个 corpus 很小时才有资格跳过 weak；500 条里只有 3 个 cache miss 仍不是 Tiny。

这一 checkpoint 没有运行新的真实 Provider 大矩阵。原因是目标本身就是降 token；批次并发、1000 条完整性、缓存、split retry、Digest budget 等先用 scripted/offline 证明，后续真实测试只做小样本探针。

## 本轮未宣称完成的能力

1. 已用 71 条真实保存来源观察（67 Change）验证当前结构门和方向非重复性，但尚未以 100–500 条**重新采集且人工抽检**的真实语料验证 Direction 的准确率、召回、漏掉的重要方向和跨期稳定性。
2. 无强 ID 的语义去重/纠错暂未实现；优先保留两条可能相关的 Change，而不是误合并。
3. 超出全局容量时明确降级，尚未实现可追溯的分层综合；不得默默截取 Top-K。
4. Code Usage 只是有界引用匹配，尚非完整调用图、跨语言可达性或执行过的兼容性测试。
5. Direction 级 Deep Dive、选择性强核验器、Coding Agent ActionPacket、Direction 通知尚未实现。现有通知和校准仍主要围绕旧 Assessment。
6. 旧 CLI/MCP 与旧 JSON/Agent 兼容路径尚未全部退役；新产品入口不得反向导入旧 Eval 实现。
7. 页面已重构且有桌面/移动端自动验收，仍需用户实际阅读后评判信息密度、中文文案与交互是否满意。
