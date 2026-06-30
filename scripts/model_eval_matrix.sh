#!/usr/bin/env bash
# Run SignalHarness real-agent model eval across local provider keys.
#
# This script intentionally never prints API keys. It maps provider-specific
# local variables from .env into SignalHarness' standard LLM_* variables for
# each run, then writes per-provider outputs plus a local comparison summary.

set -euo pipefail
set +x

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-"${ROOT_DIR}/.env"}"
FIXTURE="${MODEL_EVAL_FIXTURE:-"examples/signal_harness/sample_events.json"}"
RUNS="${MODEL_EVAL_RUNS:-1}"
OUTPUT_ROOT="${MODEL_EVAL_OUTPUT_ROOT:-"outputs/model-eval-matrix"}"
STATE_ROOT="${MODEL_EVAL_STATE_ROOT:-".signal-harness/model-eval-matrix"}"
STRICT="${MODEL_EVAL_STRICT:-0}"

usage() {
  cat <<'EOF'
Usage: scripts/model_eval_matrix.sh [options]

Options:
  --runs N          Number of runs per provider (default: MODEL_EVAL_RUNS or 1)
  --fixture PATH    Fixture path (default: examples/signal_harness/sample_events.json)
  --output-root DIR Matrix output root (default: outputs/model-eval-matrix)
  --state-root DIR  Matrix state root (default: .signal-harness/model-eval-matrix)
  --strict          Exit non-zero when any configured provider fails
  -h, --help        Show this help
EOF
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --runs)
      if [[ "$#" -lt 2 ]]; then
        echo "--runs requires a value" >&2
        exit 2
      fi
      RUNS="$2"
      shift 2
      ;;
    --fixture)
      if [[ "$#" -lt 2 ]]; then
        echo "--fixture requires a value" >&2
        exit 2
      fi
      FIXTURE="$2"
      shift 2
      ;;
    --output-root)
      if [[ "$#" -lt 2 ]]; then
        echo "--output-root requires a value" >&2
        exit 2
      fi
      OUTPUT_ROOT="$2"
      shift 2
      ;;
    --state-root)
      if [[ "$#" -lt 2 ]]; then
        echo "--state-root requires a value" >&2
        exit 2
      fi
      STATE_ROOT="$2"
      shift 2
      ;;
    --strict)
      STRICT="1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! [[ "${RUNS}" =~ ^[0-9]+$ ]] || [[ "${RUNS}" -lt 1 ]]; then
  echo "--runs must be a positive integer" >&2
  exit 2
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing env file: ${ENV_FILE}" >&2
  echo "Create it from .env.example or set ENV_FILE=/path/to/env." >&2
  exit 2
fi

if command -v git >/dev/null 2>&1; then
  if ! git -C "${ROOT_DIR}" check-ignore -q "${ENV_FILE}" 2>/dev/null; then
    echo "Warning: ${ENV_FILE} is not ignored by git. Do not commit real keys." >&2
  fi
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

cd "${ROOT_DIR}"
mkdir -p "${OUTPUT_ROOT}" "${STATE_ROOT}"

first_nonempty() {
  local name
  local value
  for name in "$@"; do
    value="${!name-}"
    if [[ -n "${value}" ]]; then
      printf '%s' "${value}"
      return 0
    fi
  done
  return 1
}

run_eval() {
  local label="$1"
  local profile="$2"
  local default_base_url="$3"
  local default_model="$4"
  local model_var="$5"
  local base_var="$6"
  shift 6
  local key
  key="$(first_nonempty "$@" || true)"
  if [[ -z "${key}" ]]; then
    echo "Skipping ${label}: no key found in expected env vars ($*)" >&2
    return 2
  fi

  local output_dir="${OUTPUT_ROOT}/${label}"
  local state_dir="${STATE_ROOT}/${label}"

  export LLM_PROVIDER="openai_compatible"
  export LLM_API_KEY="${key}"
  export LLM_BASE_URL="${!base_var:-${default_base_url}}"
  export LLM_MODEL="${!model_var:-${default_model}}"
  export LLM_MODEL_PROFILE="${profile}"

  mkdir -p "${output_dir}" "${state_dir}"
  rm -f "${output_dir}/model_eval_summary.json" "${output_dir}/model_eval_summary.md"
  echo "==> ${label}: model=${LLM_MODEL}, profile=${LLM_MODEL_PROFILE}, output=${output_dir}"
  if uv run signal-harness model-eval \
    --fixture "${FIXTURE}" \
    --mode agent \
    --profile "${profile}" \
    --runs "${RUNS}" \
    --output-dir "${output_dir}" \
    --state-dir "${state_dir}"; then
    return 0
  fi

  echo "Failed ${label}: see provider/network/schema error above. API key was not printed." >&2
  return 1
}

success_count=0
failure_count=0
skipped_count=0

providers=(
  "openai"
  "qwen"
  "kimi"
  "deepseek"
)

for provider in "${providers[@]}"; do
  case "${provider}" in
    openai)
      if run_eval "openai" "openai_gpt4o_mini" "https://api.openai.com" "gpt-4o-mini" \
        OPENAI_MODEL OPENAI_BASE_URL \
        OPENAI_API_KEY OPENAI_KEY OPENAI; then
        success_count=$((success_count + 1))
      else
        code=$?
        if [[ "${code}" -eq 2 ]]; then skipped_count=$((skipped_count + 1)); else failure_count=$((failure_count + 1)); fi
      fi
      ;;
    qwen)
      if run_eval "qwen" "qwen" "https://dashscope.aliyuncs.com/compatible-mode/v1" "qwen-plus" \
        QWEN_MODEL QWEN_BASE_URL \
        QWEN_API_KEY QWEN_KEY QWEN DASHSCOPE_API_KEY DASHSCOPE_KEY; then
        success_count=$((success_count + 1))
      else
        code=$?
        if [[ "${code}" -eq 2 ]]; then skipped_count=$((skipped_count + 1)); else failure_count=$((failure_count + 1)); fi
      fi
      ;;
    kimi)
      if run_eval "kimi" "kimi" "https://api.moonshot.cn/v1" "kimi-k2" \
        KIMI_MODEL KIMI_BASE_URL \
        KIMI_API_KEY KIMI_KEY KIMI MOONSHOT_API_KEY MOONSHOT_KEY; then
        success_count=$((success_count + 1))
      else
        code=$?
        if [[ "${code}" -eq 2 ]]; then skipped_count=$((skipped_count + 1)); else failure_count=$((failure_count + 1)); fi
      fi
      ;;
    deepseek)
      if run_eval "deepseek" "deepseek" "https://api.deepseek.com" "deepseek-chat" \
        DEEPSEEK_MODEL DEEPSEEK_BASE_URL \
        DEEPSEEK_API_KEY DEEPSEEK_KEY DEEPSEEK; then
        success_count=$((success_count + 1))
      else
        code=$?
        if [[ "${code}" -eq 2 ]]; then skipped_count=$((skipped_count + 1)); else failure_count=$((failure_count + 1)); fi
      fi
      ;;
  esac

  if [[ "${STRICT}" == "1" && "${failure_count}" -gt 0 ]]; then
    exit 1
  fi
done

rm -f "${OUTPUT_ROOT}/summary.md"
MATRIX_OUTPUT_ROOT="${OUTPUT_ROOT}" uv run python - <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path

root = Path(os.environ["MATRIX_OUTPUT_ROOT"])
rows: list[dict[str, object]] = []
for path in sorted(root.glob("*/model_eval_summary.json")):
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows.append(
        {
            "provider_dir": path.parent.name,
            "provider": payload.get("provider", "n/a"),
            "model": payload.get("model", "n/a"),
            "profile": payload.get("model_profile", "n/a"),
            "schema_valid_rate": float(payload.get("schema_valid_rate", 0)),
            "retry_rate": float(payload.get("retry_rate", 1)),
            "fallback_rate": float(payload.get("fallback_rate", 1)),
            "timeout_count": int(payload.get("timeout_count", 0)),
            "tool_error_count": int(payload.get("tool_error_count", 0)),
            "average_latency_ms": float(payload.get("average_latency_ms", 0)),
            "repair_requested_count": int(payload.get("repair_requested_count", 0)),
            "repair_executed_count": int(payload.get("repair_executed_count", 0)),
        }
    )

def score(row: dict[str, object]) -> tuple[float, float, float, int, int, float]:
    return (
        -float(row["schema_valid_rate"]),
        float(row["fallback_rate"]),
        float(row["retry_rate"]),
        int(row["timeout_count"]),
        int(row["tool_error_count"]),
        float(row["average_latency_ms"]),
    )

rows.sort(key=score)
summary = root / "summary.md"
lines = [
    "# SignalHarness Model Eval Matrix",
    "",
    "Ranking uses local SignalHarness metrics only: higher schema_valid_rate is better; lower fallback, retry, timeout, tool-error, and latency are better.",
    "",
    "| Rank | Provider | Model | Profile | Schema valid | Fallback | Retry | Timeouts | Tool errors | Latency ms | Repair requested | Repair executed |",
    "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
]
for index, row in enumerate(rows, start=1):
    lines.append(
        "| "
        + " | ".join(
            [
                str(index),
                str(row["provider_dir"]),
                str(row["model"]),
                str(row["profile"]),
                f"{float(row['schema_valid_rate']):.4f}",
                f"{float(row['fallback_rate']):.4f}",
                f"{float(row['retry_rate']):.4f}",
                str(row["timeout_count"]),
                str(row["tool_error_count"]),
                f"{float(row['average_latency_ms']):.2f}",
                str(row["repair_requested_count"]),
                str(row["repair_executed_count"]),
            ]
        )
        + " |"
    )
if rows:
    best = rows[0]
    stable = [
        row
        for row in rows
        if float(row["schema_valid_rate"]) >= 0.99
        and float(row["fallback_rate"]) == 0
        and int(row["timeout_count"]) == 0
    ]
    if stable:
        recommended = stable[0]
        recommendation = (
            f"Use **{recommended['provider_dir']} / {recommended['model']}** as the "
            "current local-fixture default candidate. Re-test before treating this "
            "as a permanent provider choice."
        )
    else:
        recommendation = (
            "No provider reached the stable threshold on this local fixture; keep "
            "mock-agent/demo as CI defaults and use real providers only for smoke tests."
        )
    lines.extend(
        [
            "",
            f"Best current candidate: **{best['provider_dir']} / {best['model']}** using profile `{best['profile']}`.",
            "",
            "## Final recommendation",
            "",
            recommendation,
            "",
        ]
    )
else:
    lines.extend(["", "No successful model_eval_summary.json files found.", ""])
summary.write_text("\n".join(lines), encoding="utf-8")
print(f"Matrix summary: {summary}")
if rows:
    best = rows[0]
    print(f"Best current candidate: {best['provider_dir']} / {best['model']} ({best['profile']})")
PY

echo "Completed: success=${success_count}, failed=${failure_count}, skipped=${skipped_count}"
if [[ "${success_count}" -eq 0 ]]; then
  exit 1
fi
if [[ "${STRICT}" == "1" && "${failure_count}" -gt 0 ]]; then
  exit 1
fi
