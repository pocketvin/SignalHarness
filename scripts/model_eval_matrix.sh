#!/usr/bin/env bash
# Run SignalHarness real-agent model eval across local provider keys.
#
# The script intentionally never prints API keys. It sources a local .env,
# maps provider-specific variables into SignalHarness' standard LLM_* variables,
# and writes provider-isolated outputs plus a local comparison summary.

set -uo pipefail
set +x

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-"${ROOT_DIR}/.env"}"
FIXTURE="${MODEL_EVAL_FIXTURE:-"examples/signal_harness/sample_events.json"}"
RUNS="${MODEL_EVAL_RUNS:-1}"
OUTPUT_ROOT="${MODEL_EVAL_OUTPUT_ROOT:-"outputs/model-eval-matrix"}"
STATE_ROOT="${MODEL_EVAL_STATE_ROOT:-".signal-harness/model-eval-matrix"}"
STRICT="${MODEL_EVAL_STRICT:-0}"
PROVIDERS_CSV="${MODEL_EVAL_PROVIDERS:-"openai,qwen,kimi,deepseek"}"
SLEEP_SECONDS="${MODEL_EVAL_SLEEP_SECONDS:-0}"
REQUEST_SLEEP_SECONDS="${MODEL_EVAL_REQUEST_SLEEP_SECONDS:-}"

usage() {
  cat <<'EOF'
Usage: scripts/model_eval_matrix.sh [options]

Options:
  --providers CSV   Providers to run, comma-separated (default: openai,qwen,kimi,deepseek)
  --runs N          Number of runs per provider (default: MODEL_EVAL_RUNS or 1)
  --sleep N         Seconds to sleep before each provider run (default: 0);
                    also enables 1s provider request pacing unless
                    MODEL_EVAL_REQUEST_SLEEP_SECONDS is set
  --fixture PATH    Fixture path (default: examples/signal_harness/sample_events.json)
  --output-root DIR Matrix output root (default: outputs/model-eval-matrix)
  --state-root DIR  Matrix state root (default: .signal-harness/model-eval-matrix)
  --strict          Exit non-zero when any configured provider fails
  -h, --help        Show this help
EOF
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --providers)
      if [[ "$#" -lt 2 ]]; then
        echo "--providers requires a comma-separated value" >&2
        exit 2
      fi
      PROVIDERS_CSV="$2"
      shift 2
      ;;
    --runs)
      if [[ "$#" -lt 2 ]]; then
        echo "--runs requires a value" >&2
        exit 2
      fi
      RUNS="$2"
      shift 2
      ;;
    --sleep)
      if [[ "$#" -lt 2 ]]; then
        echo "--sleep requires a value" >&2
        exit 2
      fi
      SLEEP_SECONDS="$2"
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

if ! [[ "${SLEEP_SECONDS}" =~ ^[0-9]+$ ]]; then
  echo "--sleep must be a non-negative integer" >&2
  exit 2
fi

if [[ -z "${REQUEST_SLEEP_SECONDS}" ]]; then
  REQUEST_SLEEP_SECONDS="0"
  if [[ "${SLEEP_SECONDS}" -gt 0 ]]; then
    REQUEST_SLEEP_SECONDS="1"
  fi
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

cd "${ROOT_DIR}" || exit 2
mkdir -p "${OUTPUT_ROOT}" "${STATE_ROOT}"

IFS=',' read -r -a requested_providers <<< "${PROVIDERS_CSV}"
providers=()
for raw_provider in "${requested_providers[@]}"; do
  provider="$(printf '%s' "${raw_provider}" | tr '[:upper:]' '[:lower:]' | xargs)"
  case "${provider}" in
    openai|qwen|kimi|deepseek)
      providers+=("${provider}")
      ;;
    "")
      ;;
    *)
      echo "Unknown provider in --providers: ${raw_provider}" >&2
      exit 2
      ;;
  esac
done

if [[ "${#providers[@]}" -eq 0 ]]; then
  echo "--providers selected no providers" >&2
  exit 2
fi

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

classify_log() {
  local log_file="$1"
  if [[ ! -f "${log_file}" ]]; then
    printf 'unknown_error'
    return 0
  fi
  if grep -Eiq '429 Too Many|HTTP 429|status code 429|Error code: 429|Too Many Requests|rate.?limit' "${log_file}"; then
    printf 'rate_limited'
  elif grep -Eiq 'provider_timeout|timed out|timeout' "${log_file}"; then
    printf 'timeout'
  elif grep -Eiq 'HTTPStatusError|Client error|Server error|Bad Gateway|Service Unavailable' "${log_file}"; then
    printf 'http_error'
  elif grep -Eiq 'JSONDecodeError|Expecting value|invalid json|Agent response was empty|Agent response must be a JSON object' "${log_file}"; then
    printf 'json_parse_error'
  elif grep -Eiq 'validation error|schema validation|Field required|extra inputs' "${log_file}"; then
    printf 'schema_error'
  elif grep -Eiq 'coverage' "${log_file}"; then
    printf 'coverage_error'
  else
    printf 'unknown_error'
  fi
}

write_status() {
  local status_file="$1"
  local status="$2"
  local error_class="$3"
  PROVIDER_STATUS_FILE="${status_file}" \
  PROVIDER_STATUS="${status}" \
  PROVIDER_ERROR_CLASS="${error_class}" \
    uv run python - <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path

path = Path(os.environ["PROVIDER_STATUS_FILE"])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(
    json.dumps(
        {
            "status": os.environ["PROVIDER_STATUS"],
            "error_class": os.environ["PROVIDER_ERROR_CLASS"],
        },
        indent=2,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
PY
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

  local output_dir="${OUTPUT_ROOT}/${label}"
  local state_dir="${STATE_ROOT}/${label}"
  local log_file="${output_dir}/run.log"
  local status_file="${output_dir}/provider_status.json"
  mkdir -p "${output_dir}" "${state_dir}"
  rm -f \
    "${output_dir}/model_eval_summary.json" \
    "${output_dir}/model_eval_summary.md" \
    "${output_dir}/task_trace.json" \
    "${log_file}" \
    "${status_file}"

  if [[ -z "${key}" ]]; then
    echo "Skipping ${label}: no key found in expected env vars ($*)" >&2
    write_status "${status_file}" "skipped" "unknown_error"
    return 2
  fi

  export LLM_PROVIDER="openai_compatible"
  export LLM_API_KEY="${key}"
  export LLM_BASE_URL="${!base_var:-${default_base_url}}"
  export LLM_MODEL="${!model_var:-${default_model}}"
  export LLM_MODEL_PROFILE="${profile}"
  export LLM_REQUEST_SLEEP_SECONDS="${REQUEST_SLEEP_SECONDS}"

  if [[ "${SLEEP_SECONDS}" -gt 0 ]]; then
    echo "Sleeping ${SLEEP_SECONDS}s before ${label} run..."
    sleep "${SLEEP_SECONDS}"
  fi

  echo "==> ${label}: model=${LLM_MODEL}, profile=${LLM_MODEL_PROFILE}, output=${output_dir}"
  if uv run signal-harness model-eval \
    --fixture "${FIXTURE}" \
    --mode agent \
    --profile "${profile}" \
    --runs "${RUNS}" \
    --output-dir "${output_dir}" \
    --state-dir "${state_dir}" \
    >"${log_file}" 2>&1; then
    write_status "${status_file}" "success" "unknown_error"
    echo "Finished ${label}: summary=${output_dir}/model_eval_summary.json"
    return 0
  fi

  local error_class
  error_class="$(classify_log "${log_file}")"
  write_status "${status_file}" "failed" "${error_class}"
  echo "Failed ${label}: class=${error_class}; see ${log_file}. API key was not printed." >&2
  return 1
}

success_count=0
failure_count=0
skipped_count=0

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
      if run_eval "kimi" "kimi" "https://api.moonshot.cn/v1" "kimi-k2.7-code" \
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
    break
  fi
done

rm -f "${OUTPUT_ROOT}/summary.md"
MATRIX_OUTPUT_ROOT="${OUTPUT_ROOT}" \
MATRIX_PROVIDER_LIST="$(IFS=','; echo "${providers[*]}")" \
MATRIX_RUNS="${RUNS}" \
MATRIX_FIXTURE="${FIXTURE}" \
  uv run python - <<'PY'
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

root = Path(os.environ["MATRIX_OUTPUT_ROOT"])
requested = [
    item.strip()
    for item in os.environ["MATRIX_PROVIDER_LIST"].split(",")
    if item.strip()
]


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_trace_errors(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    errors: list[str] = []
    for step in payload:
        if not isinstance(step, dict):
            continue
        for key in ("schema_error", "error"):
            value = step.get(key)
            if isinstance(value, str) and value:
                errors.append(value)
    return errors


def classify_provider_error(messages: list[str]) -> str:
    text = "\n".join(messages).lower()
    if not text:
        return "none"
    if (
        "429 too many" in text
        or "http 429" in text
        or "status code 429" in text
        or "error code: 429" in text
        or "too many requests" in text
        or "rate limit" in text
    ):
        return "rate_limited"
    if "provider_timeout" in text or "timed out" in text or "timeout" in text:
        return "timeout"
    if (
        "httpstatuserror" in text
        or "client error" in text
        or "server error" in text
        or "bad gateway" in text
        or "service unavailable" in text
    ):
        return "http_error"
    if (
        "jsondecodeerror" in text
        or "expecting value" in text
        or "invalid json" in text
        or "agent response was empty" in text
        or "agent response must be a json object" in text
    ):
        return "json_parse_error"
    if "coverage" in text:
        return "coverage_error"
    if "validation error" in text or "schema validation" in text or "field required" in text:
        return "schema_error"
    return "unknown_error"


rows: list[dict[str, object]] = []
for provider_dir in requested:
    provider_root = root / provider_dir
    payload = load_json(provider_root / "model_eval_summary.json")
    status_payload = load_json(provider_root / "provider_status.json")
    provider_classes = Counter(
        {
            str(key): int(value)
            for key, value in payload.get("provider_error_classes", {}).items()
            if isinstance(key, str) and isinstance(value, int)
        }
    )
    if provider_classes:
        classified = provider_classes.most_common(1)[0][0]
    else:
        trace_class = classify_provider_error(load_trace_errors(provider_root / "task_trace.json"))
        classified = (
            str(status_payload.get("error_class") or "unknown_error")
            if trace_class == "none"
            else trace_class
        )
    schema_valid_rate = float(payload.get("schema_valid_rate", 0))
    retry_rate = float(payload.get("retry_rate", 1))
    fallback_rate = float(payload.get("fallback_rate", 1))
    timeout_count = int(payload.get("timeout_count", 0))
    tool_validation_error_count = int(payload.get("tool_validation_error_count", 0))
    tool_blocked_count = int(payload.get("tool_blocked_count", 0))
    tool_budget_error_count = int(payload.get("tool_budget_error_count", 0))
    tool_runtime_error_count = int(payload.get("tool_runtime_error_count", 0))
    total_tool_error_count = int(payload.get("total_tool_error_count", 0))
    legacy_tool_error_count = int(payload.get("tool_error_count", 0))
    average_latency_ms = float(payload.get("average_latency_ms", 0))
    repair_requested_count = int(payload.get("repair_requested_count", 0))
    repair_executed_count = int(payload.get("repair_executed_count", 0))

    status = str(status_payload.get("status") or ("success" if payload else "missing"))
    if status == "skipped":
        result = "skipped"
    elif classified == "rate_limited":
        result = "inconclusive"
        status = "rate_limited"
    elif status == "success":
        is_stable = (
            schema_valid_rate >= 0.99
            and fallback_rate == 0
            and retry_rate == 0
            and timeout_count == 0
            and total_tool_error_count == 0
        )
        result = "complete_stable" if is_stable else "complete_unstable"
    elif status == "failed":
        result = "failed"
    else:
        result = "inconclusive"
    rows.append(
        {
            "provider_dir": provider_dir,
            "provider": payload.get("provider", "n/a"),
            "model": payload.get("model", "n/a"),
            "profile": payload.get("model_profile", "n/a"),
            "status": status,
            "result": result,
            "error_class": classified,
            "schema_valid_rate": schema_valid_rate,
            "retry_rate": retry_rate,
            "fallback_rate": fallback_rate,
            "timeout_count": timeout_count,
            "tool_validation_error_count": tool_validation_error_count,
            "tool_blocked_count": tool_blocked_count,
            "tool_budget_error_count": tool_budget_error_count,
            "tool_runtime_error_count": tool_runtime_error_count,
            "total_tool_error_count": total_tool_error_count,
            "legacy_tool_error_count": legacy_tool_error_count,
            "average_latency_ms": average_latency_ms,
            "repair_requested_count": repair_requested_count,
            "repair_executed_count": repair_executed_count,
        }
    )


def score(row: dict[str, object]) -> tuple[int, float, float, float, int, int, float]:
    result_penalty = {
        "complete_stable": 0,
        "complete_unstable": 1,
        "inconclusive": 2,
        "failed": 3,
        "skipped": 4,
    }.get(str(row["result"]), 5)
    return (
        result_penalty,
        -float(row["schema_valid_rate"]),
        float(row["fallback_rate"]),
        float(row["retry_rate"]),
        int(row["timeout_count"]),
        int(row["total_tool_error_count"]),
        float(row["average_latency_ms"]),
    )


ranked_rows = sorted(rows, key=score)
summary = root / "summary.md"
lines = [
    "# SignalHarness Model Eval Matrix",
    "",
    "This is a current local fixture result, not a permanent model ranking.",
    f"Fixture: `{os.environ['MATRIX_FIXTURE']}`; runs per provider: {os.environ['MATRIX_RUNS']}.",
    "",
    "Ranking uses local SignalHarness metrics only: higher schema_valid_rate is better; lower fallback, retry, timeout, tool-error, and latency are better. Rate-limited providers are marked inconclusive.",
    "",
    "Result labels: `complete_stable` means schema_valid_rate >= 0.99, fallback_rate == 0, retry_rate == 0, timeout_count == 0, and total_tool_error_count == 0; `complete_unstable` means the command completed but at least one fallback, retry, timeout, or tool error occurred; `inconclusive` means rate-limited or provider hard failure; `failed` means the provider command failed; `skipped` means no key was configured.",
    "",
    "| Rank | Provider | Model | Profile | Status | Result | Error class | Schema valid | Fallback | Retry | Timeouts | Tool validation | Tool blocked | Tool budget | Tool runtime | Tool total | Latency ms | Repair requested | Repair executed |",
    "|---:|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
]
for index, row in enumerate(ranked_rows, start=1):
    lines.append(
        "| "
        + " | ".join(
            [
                str(index),
                str(row["provider_dir"]),
                str(row["model"]),
                str(row["profile"]),
                str(row["status"]),
                str(row["result"]),
                str(row["error_class"]),
                f"{float(row['schema_valid_rate']):.4f}",
                f"{float(row['fallback_rate']):.4f}",
                f"{float(row['retry_rate']):.4f}",
                str(row["timeout_count"]),
                str(row["tool_validation_error_count"]),
                str(row["tool_blocked_count"]),
                str(row["tool_budget_error_count"]),
                str(row["tool_runtime_error_count"]),
                str(row["total_tool_error_count"]),
                f"{float(row['average_latency_ms']):.2f}",
                str(row["repair_requested_count"]),
                str(row["repair_executed_count"]),
            ]
        )
        + " |"
    )

stable_rows = [row for row in ranked_rows if row["result"] == "complete_stable"]
completed_rows = [
    row
    for row in ranked_rows
    if str(row["result"]) in {"complete_stable", "complete_unstable"}
]

lines.extend(["", "## Tool error breakdown", ""])
for row in ranked_rows:
    lines.append(
        "- "
        f"{row['provider_dir']}: validation={row['tool_validation_error_count']}, "
        f"harmless_guard_block={row['tool_blocked_count']}, "
        f"budget={row['tool_budget_error_count']}, "
        f"runtime={row['tool_runtime_error_count']}, "
        f"total={row['total_tool_error_count']}."
    )
lines.extend(
    [
        "",
        "Validation errors indicate model tool argument adherence issues, such as missing `action`, `repo`, `url`, or `fixture`.",
        "Blocked-tool counts are harmless guard blocks when Python rejects non-allowlisted or non-read-only requests.",
        "",
    ]
)

if stable_rows:
    recommended = stable_rows[0]
    recommendation = (
        f"Use **{recommended['provider_dir']} / {recommended['model']}** as the "
        "current local-fixture default candidate. Re-test before treating this "
        "as a durable provider choice."
    )
else:
    recommendation = (
        "No stable provider on this fixture. Keep mock-agent/demo as CI defaults and "
        "do not choose a real-provider default from this run."
    )

lines.extend(
    [
        "## Final recommendation",
        "",
        recommendation,
        "",
    ]
)
summary.write_text("\n".join(lines), encoding="utf-8")
print(f"Matrix summary: {summary}")
if stable_rows:
    best = stable_rows[0]
    print(f"Best stable candidate: {best['provider_dir']} / {best['model']} ({best['profile']})")
elif completed_rows:
    print("No stable provider on this fixture; completed providers are unstable.")
else:
    print("No stable provider on this fixture; matrix is inconclusive.")
PY

echo "Completed: success=${success_count}, failed=${failure_count}, skipped=${skipped_count}"
if [[ "${success_count}" -eq 0 ]]; then
  exit 1
fi
if [[ "${STRICT}" == "1" && "${failure_count}" -gt 0 ]]; then
  exit 1
fi
