# SignalHarness Demo Guide

这份指南用于面试前准备和现场演示。所有命令都只写本地 outputs，不会提交 `.env`、`outputs/` 或 `.signal-harness/`。

## 1. Offline stable demo

适合截图、README、面试前预演和网络不稳定环境。无需 API key。

```bash
uv run signal-harness scan \
  --fixture examples/signal_harness/curated_showcase_events.json \
  --mode mock-agent \
  --output-dir outputs/curated-showcase \
  --state-dir .signal-harness/curated-showcase

uv run signal-harness dashboard --output-dir outputs/curated-showcase
uv run signal-harness trace --output-dir outputs/curated-showcase
uv run signal-harness digest --output-dir outputs/curated-showcase

open outputs/curated-showcase/dashboard.html
```

说明：

- 无需 API key。
- 不依赖真实网络。
- 使用 scripted mock provider，但仍走真实 five-Agent architecture。
- 适合截图和稳定面试演示。

## 2. Live OpenAI showcase

适合展示真实 provider call、真实 watchlist source collection、schema validation、source health、trace 和 dashboard。

```bash
set -a
source .env
set +a

OPENAI_SELECTED_KEY="${OPENAI_API_KEY:-${OPENAI_KEY:-${OPENAI:-}}}"

export LLM_PROVIDER="openai_compatible"
export LLM_API_KEY="$OPENAI_SELECTED_KEY"
export LLM_BASE_URL="${OPENAI_BASE_URL:-https://api.openai.com}"
export LLM_MODEL="${OPENAI_MODEL:-gpt-4o-mini}"
export LLM_MODEL_PROFILE="openai_gpt4o_mini"

SINCE="$(uv run python - <<'PY'
from datetime import datetime, timedelta, timezone
value = datetime.now(timezone.utc) - timedelta(days=14)
print(value.replace(microsecond=0).isoformat().replace("+00:00", "Z"))
PY
)"

uv run signal-harness scan \
  --mode agent \
  --since "$SINCE" \
  --max-events 12 \
  --max-events-per-source 5 \
  --output-dir outputs/openai-live-showcase-v4 \
  --state-dir .signal-harness/openai-live-showcase-v4

uv run signal-harness dashboard --output-dir outputs/openai-live-showcase-v4
uv run signal-harness trace --output-dir outputs/openai-live-showcase-v4
uv run signal-harness digest --output-dir outputs/openai-live-showcase-v4

open outputs/openai-live-showcase-v4/dashboard.html
```

注意：

- 不传 `--fixture`，这样才会读取 `configs/watchlist.yaml`。
- `.env` 只保留在本地，不提交。
- live run 可能出现 source failure，这是正常且应该展示的，不要隐藏。
- 如果 provider retry/fallback/schema failure 出现，也应该在 dashboard/trace 中解释，而不是当作页面 bug。

## 3. 展示时看哪些区块

建议按这个顺序讲：

1. Executive Summary  
   快速说明 collected signals、assessments、alerts、action_required 和 source health。

2. Signal Summary  
   说明本轮发生了什么、为什么重要、下一步建议是什么。

3. No high-priority signals / Top observed signals  
   如果 alerts/action_required 为 0，强调系统没有把普通外部信息强行升级成高优先级告警。

4. Source health  
   展示 GitHub/RSS/Web source task 状态。live run 出现 source failure 时，说明这是诚实可审计的表现。

5. Tool health  
   说明 controlled tool-use loop：LLM 请求工具，Python 决定是否执行并记录错误/阻断。

6. Model, profile, and limits  
   展示 provider、model、model profile、schema retry、tool budget、repair limit 等边界。

7. Agent trace and tools  
   展示每个 Agent 的 input/output、tools_requested、tools_executed、permission checks。

8. Score breakdown  
   说明最终分数不是 LLM 直接决定，而是 deterministic base、semantic relevance、evidence confidence 和 policy multiplier 的 guarded blend。

9. Learning review-only section  
   说明 LearningPolicyAgent 只能产出 proposal，不会自动改配置。

## 4. 本地产物边界

- `outputs/` 是本地运行产物，不提交。
- `.signal-harness/` 是本地状态，不提交。
- `.env` 是本地密钥配置，不提交。
- raw provider response 不提交。
- 截图可以在本地打开 dashboard 后手动添加；本轮没有自动生成截图，避免把本地运行细节误提交。
