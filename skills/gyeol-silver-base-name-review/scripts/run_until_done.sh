#!/usr/bin/env bash
set -euo pipefail

WORKDIR="${WORKDIR:-/home/oogie/gyeol/data}"
DB_DSN="${DB_URL:-postgresql://gyeol:fXMVzWf0NQTAnxLdWj5wla2UlVm4dkbAEBAe8Qc@localhost:5432/gyeol}"
SLEEP_SECS="${SLEEP_SECS:-1}"
MAX_STALLS="${MAX_STALLS:-1}"
RUN_STARTED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
PLATFORM_FILTER=""
BRAND_ID_FILTER=""
CATEGORY_FILTER=""
LOG_DIR="${LOG_DIR:-}"
LOCK_FILE="${LOCK_FILE:-}"
STATUS_FILE="${STATUS_FILE:-}"
PROGRESS_LOG="${PROGRESS_LOG:-}"
CURRENT_LOG_LINK="${CURRENT_LOG_LINK:-}"
PID_FILE="${PID_FILE:-}"
LAST_MESSAGE_FILE="${LAST_MESSAGE_FILE:-}"
CURRENT_MESSAGE=""
CURRENT_BEFORE=""
CURRENT_AFTER=""
CURRENT_UPDATED=""
CURRENT_BATCH_LOG=""

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

build_where_clause() {
  local where="platform = '${PLATFORM_FILTER}' AND platform_brand_id = '${BRAND_ID_FILTER}' AND color IS NULL"
  if [[ -n "$CATEGORY_FILTER" ]]; then
    where="${where} AND category = '${CATEGORY_FILTER}'"
  fi
  printf '%s' "$where"
}

count_nulls() {
  local where_clause
  where_clause="$(build_where_clause)"
  psql "$DB_DSN" -At -c "SELECT count(*) FROM staging.product WHERE ${where_clause};"
}

log() {
  local line
  line="[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"
  printf '%s\n' "$line" | tee -a "$PROGRESS_LOG"
}

write_status() {
  local state="${1:-running}"
  cat > "$STATUS_FILE" <<EOF
state=$state
pid=$$
started_at=$RUN_STARTED_AT
updated_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
remaining_before=$CURRENT_BEFORE
remaining_after=$CURRENT_AFTER
updated_last_batch=$CURRENT_UPDATED
stall_count=$stall_count
current_log=$CURRENT_BATCH_LOG
last_message_file=$LAST_MESSAGE_FILE
message=$CURRENT_MESSAGE
platform=$PLATFORM_FILTER
brand_id=$BRAND_ID_FILTER
category=$CATEGORY_FILTER
EOF
  printf '%s\n' "$$" > "$PID_FILE"
}

finish() {
  local state="$1"
  local message="$2"
  local exit_code="${3:-0}"
  CURRENT_MESSAGE="$message"
  write_status "$state"
  log "$message"
  exit "$exit_code"
}

scope_slug() {
  local slug="${PLATFORM_FILTER}__${BRAND_ID_FILTER}"
  if [[ -n "$CATEGORY_FILTER" ]]; then
    slug="${slug}__${CATEGORY_FILTER}"
  fi
  printf '%s' "$slug" | tr -c 'A-Za-z0-9._-' '_'
}

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
    --category)
      CATEGORY_FILTER="${2:?missing value for --category}"
      shift 2
      ;;
    --db-dsn)
      DB_DSN="${2:?missing value for --db-dsn}"
      shift 2
      ;;
    --sleep-secs)
      SLEEP_SECS="${2:?missing value for --sleep-secs}"
      shift 2
      ;;
    --max-stalls)
      MAX_STALLS="${2:?missing value for --max-stalls}"
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

need_cmd codex
need_cmd psql
need_cmd flock

scope="$(scope_slug)"
LOG_DIR="${LOG_DIR:-/tmp/gyeol-silver-base-name-review/${scope}}"
LOCK_FILE="${LOCK_FILE:-/tmp/gyeol-silver-base-name-review.${scope}.lock}"
STATUS_FILE="${STATUS_FILE:-$LOG_DIR/status.txt}"
PROGRESS_LOG="${PROGRESS_LOG:-$LOG_DIR/progress.log}"
CURRENT_LOG_LINK="${CURRENT_LOG_LINK:-$LOG_DIR/current.log}"
PID_FILE="${PID_FILE:-$LOG_DIR/runner.pid}"
LAST_MESSAGE_FILE="${LAST_MESSAGE_FILE:-$LOG_DIR/last_message.txt}"

mkdir -p "$LOG_DIR"
exec 9>"$LOCK_FILE"
flock -n 9 || {
  echo "Another base_name runner is already active for this scope." >&2
  exit 1
}

PROMPT="$(cat <<EOF
\$gyeol-silver-base-name-review
상품명을 보고 같은 제품의 색상 변형을 판단해서 base_name과 color를 채워.
color IS NULL인 상품 50개씩 처리하고 direct psql UPDATE만 실행해.
base_name이 이미 있으면 그대로 유지하고 color만 채워도 됨.
원본 casing 유지. 대문자 변환 하지 마.
최종 답변은 짧게 updated_rows만 보고해.
대상 범위는 platform=${PLATFORM_FILTER} AND platform_brand_id=${BRAND_ID_FILTER} 만이다.
EOF
)"

if [[ -n "$CATEGORY_FILTER" ]]; then
  PROMPT="${PROMPT}"$'\n'"추가 조건: category='${CATEGORY_FILTER}' 인 상품만 대상이다."
fi

stall_count=0
batch_no=0
CURRENT_MESSAGE="Runner started"
write_status "starting"

trap 'finish stopped "Runner interrupted." 130' INT TERM

while true; do
  before_count="$(count_nulls)"
  CURRENT_BEFORE="$before_count"
  CURRENT_AFTER="$before_count"
  CURRENT_UPDATED=""

  if [[ "$before_count" == "0" ]]; then
    finish completed "No NULL color rows remain in scope. Exiting." 0
  fi

  batch_no=$((batch_no + 1))
  batch_raw_tmp="$(mktemp "$LOG_DIR/.batch_${batch_no}_XXXXXX.raw")"
  batch_msg_tmp="$(mktemp "$LOG_DIR/.batch_${batch_no}_XXXXXX.msg")"
  batch_failed_log="$LOG_DIR/failed_batch_$(date -u '+%Y%m%dT%H%M%SZ')_${batch_no}.log"
  CURRENT_BATCH_LOG=""
  CURRENT_MESSAGE="Starting batch ${batch_no}. remaining_before=${before_count}."
  write_status "running"
  log "Starting batch ${batch_no}. remaining_before=${before_count}"

  if ! printf '%s\n' "$PROMPT" | codex exec --dangerously-bypass-approvals-and-sandbox --color never -o "$batch_msg_tmp" -C "$WORKDIR" - >"$batch_raw_tmp" 2>&1; then
    mv "$batch_raw_tmp" "$batch_failed_log"
    ln -sfn "$batch_failed_log" "$CURRENT_LOG_LINK"
    CURRENT_BATCH_LOG="$batch_failed_log"
    if [[ -s "$batch_msg_tmp" ]]; then
      mv "$batch_msg_tmp" "$LAST_MESSAGE_FILE"
    else
      rm -f "$batch_msg_tmp"
    fi
    finish failed "Codex batch ${batch_no} failed. See ${batch_failed_log}" 1
  fi

  rm -f "$batch_raw_tmp"
  rm -f "$CURRENT_LOG_LINK"
  CURRENT_BATCH_LOG=""
  if [[ -s "$batch_msg_tmp" ]]; then
    mv "$batch_msg_tmp" "$LAST_MESSAGE_FILE"
  else
    : > "$LAST_MESSAGE_FILE"
    rm -f "$batch_msg_tmp"
  fi

  after_count="$(count_nulls)"
  CURRENT_AFTER="$after_count"

  if [[ "$after_count" -lt "$before_count" ]]; then
    progress=$((before_count - after_count))
    CURRENT_UPDATED="$progress"
    stall_count=0
    CURRENT_MESSAGE="Batch ${batch_no} completed. updated=${progress}. remaining_after=${after_count}."
    write_status "running"
    log "Batch ${batch_no} completed. updated=${progress}. remaining_after=${after_count}"
  else
    stall_count=$((stall_count + 1))
    CURRENT_UPDATED="0"
    CURRENT_MESSAGE="Batch ${batch_no} made no progress. remaining_after=${after_count}. stall_count=${stall_count}."
    write_status "running"
    log "Batch ${batch_no} made no progress. remaining_after=${after_count}. stall_count=${stall_count}"
    if [[ "$stall_count" -ge "$MAX_STALLS" ]]; then
      finish stalled "Stopping because progress stalled. Manual review rows likely remain." 2
    fi
  fi

  sleep "$SLEEP_SECS"
done
