#!/usr/bin/env bash
set -euo pipefail

DB_DSN="${DB_URL:-postgresql://gyeol:fXMVzWf0NQTAnxLdWj5wla2UlVm4dkbAEBAe8Qc@localhost:5432/gyeol}"
PLATFORM_FILTER=""
BRAND_ID_FILTER=""
LOG_DIR="${LOG_DIR:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform)
      PLATFORM_FILTER="${2:?missing value for --platform}"
      shift 2
      ;;
    --brand-id)
      BRAND_ID_FILTER="${2:?missing value for --brand-id}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$PLATFORM_FILTER" || -z "$BRAND_ID_FILTER" ]]; then
  echo "--platform and --brand-id are required" >&2
  exit 1
fi

if [[ -z "$LOG_DIR" ]]; then
  scope="$(printf '%s__%s' "$PLATFORM_FILTER" "$BRAND_ID_FILTER" | tr -c 'A-Za-z0-9._-' '_')"
  LOG_DIR="/tmp/gyeol-silver-base-name-review/${scope}"
fi

STATUS_FILE="${STATUS_FILE:-$LOG_DIR/status.txt}"
CURRENT_LOG_LINK="${CURRENT_LOG_LINK:-$LOG_DIR/current.log}"

state="unknown"
pid=""
started_at=""
updated_at=""
remaining_before=""
remaining_after=""
updated_last_batch=""
stall_count=""
current_log=""
last_message_file=""
message=""

if [[ -f "$STATUS_FILE" ]]; then
  while IFS='=' read -r key value; do
    case "$key" in
      state) state="$value" ;;
      pid) pid="$value" ;;
      started_at) started_at="$value" ;;
      updated_at) updated_at="$value" ;;
      remaining_before) remaining_before="$value" ;;
      remaining_after) remaining_after="$value" ;;
      updated_last_batch) updated_last_batch="$value" ;;
      stall_count) stall_count="$value" ;;
      current_log) current_log="$value" ;;
      last_message_file) last_message_file="$value" ;;
      message) message="$value" ;;
    esac
  done < "$STATUS_FILE"
fi

if [[ -z "$current_log" && -L "$CURRENT_LOG_LINK" ]]; then
  current_log="$(readlink -f "$CURRENT_LOG_LINK" || true)"
fi

null_remaining="$(psql "$DB_DSN" -At -c "SELECT count(*) FROM staging.product WHERE platform = '${PLATFORM_FILTER}' AND platform_brand_id = '${BRAND_ID_FILTER}' AND (base_name IS NULL OR base_name = '');" 2>/dev/null || printf '?')"
runner_pid="$(ps -ef | awk '/gyeol-silver-base-name-review\/scripts\/run_until_done\.sh/ && !/awk/ {print $2; exit}')"

if [[ -n "$runner_pid" ]]; then
  printf 'process_state=running\n'
else
  printf 'process_state=stopped\n'
fi
printf 'runner_pid=%s\n' "${runner_pid:-}"
printf 'status_state=%s\n' "$state"
printf 'null_remaining=%s\n' "$null_remaining"
printf 'remaining_before=%s\n' "$remaining_before"
printf 'remaining_after=%s\n' "$remaining_after"
printf 'updated_last_batch=%s\n' "$updated_last_batch"
printf 'stall_count=%s\n' "$stall_count"
printf 'started_at=%s\n' "$started_at"
printf 'updated_at=%s\n' "$updated_at"
printf 'current_log=%s\n' "$current_log"
printf 'last_message_file=%s\n' "$last_message_file"
printf 'message=%s\n' "$message"
