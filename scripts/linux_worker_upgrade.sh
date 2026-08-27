#!/usr/bin/env bash
set -Eeuo pipefail

MODE="${1:---schedule}"
CONFIG="${CHAT2API_WORKER_CONFIG:-/etc/chat2api-worker/worker.json}"
STATE_DIR="${CHAT2API_WORKER_STATE_DIR:-/var/lib/chat2api-worker}"
STATE_FILE="${STATE_DIR}/upgrade-state.json"
LOG_FILE="${STATE_DIR}/upgrade.log"
LOCK_FILE="/run/chat2api-worker-upgrade.lock"

log() {
  printf '%s [chat2api-worker-upgrade] %s\n' "$(date -Is)" "$*"
}

load_identity() {
  [[ -r "$CONFIG" ]] || return 1
  WORKER_ID="$(jq -er '.worker_id | select(type == "string" and length > 0)' "$CONFIG")"
  WORKER_TOKEN="$(jq -er '.worker_token | select(type == "string" and length > 0)' "$CONFIG")"
  WS_URL="$(jq -er '.websocket_url | select(type == "string" and length > 0)' "$CONFIG")"
  SERVER_URL="$(python3 - "$WS_URL" <<'PY'
from urllib.parse import urlsplit, urlunsplit
import sys
p=urlsplit(sys.argv[1])
if p.scheme not in {'ws','wss'} or not p.netloc:
    raise SystemExit(1)
scheme='https' if p.scheme == 'wss' else 'http'
print(urlunsplit((scheme,p.netloc,'','','')).rstrip('/'))
PY
)"
  [[ "$SERVER_URL" == https://* || "${CHAT2API_ALLOW_INSECURE_HTTP:-0}" == "1" ]]
}

write_local_state() {
  local state="$1" stage="$2" percent="$3" message="$4"
  install -d -m 0755 "$STATE_DIR"
  jq -n \
    --arg state "$state" \
    --arg stage "$stage" \
    --arg message "$message" \
    --argjson percent "$percent" \
    --arg updated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{state:$state,stage:$stage,percent:$percent,message:$message,updated_at:$updated_at}' \
    >"${STATE_FILE}.tmp"
  mv "${STATE_FILE}.tmp" "$STATE_FILE"
}

report() {
  local state="$1" stage="$2" percent="$3" message="$4"
  message="${message//$'\r'/ }"
  message="${message//$'\n'/ }"
  message="${message:0:700}"
  write_local_state "$state" "$stage" "$percent" "$message"
  jq -n \
    --arg state "$state" \
    --arg stage "$stage" \
    --arg message "$message" \
    --argjson percent "$percent" \
    '{state:$state,stage:$stage,percent:$percent,message:$message}' \
    | curl -fsS --connect-timeout 5 --max-time 10 \
        -H 'Content-Type: application/json' \
        -H "X-Worker-ID: ${WORKER_ID}" \
        -H "X-Worker-Token: ${WORKER_TOKEN}" \
        --data-binary @- \
        "${SERVER_URL}/api/workers/${WORKER_ID}/upgrade-progress" >/dev/null 2>&1 || true
}

stage_percent() {
  case "$1" in
    starting) echo 2;;
    system-check) echo 6;;
    cleanup) echo 10;;
    packages) echo 20;;
    worker-bundle) echo 42;;
    python) echo 58;;
    xray) echo 67;;
    enrollment) echo 74;;
    systemd) echo 84;;
    health) echo 94;;
    complete) echo 100;;
    *) echo 5;;
  esac
}

run_upgrade() {
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    report running queued 1 "已有 Worker 更新任务正在执行"
    return 0
  fi

  # The request arrives through the Agent. Give it time to return command.result
  # before bootstrap restarts chat2api-worker-agent.service.
  sleep 2
  install -d -m 0755 "$STATE_DIR"
  : >"$LOG_FILE"
  report running starting 2 "正在准备在线更新 Worker"

  local bootstrap rc
  bootstrap="$(mktemp)"
  trap 'rm -f "$bootstrap"' EXIT
  report running download 4 "从中心服务器下载最新 Worker 安装器"
  if ! curl -fSL --retry 3 --retry-delay 1 --retry-all-errors --connect-timeout 8 --max-time 60 \
      -o "$bootstrap" "${SERVER_URL}/bootstrap/linux-worker.sh"; then
    report failed download 4 "下载最新 Worker 安装器失败"
    return 1
  fi
  chmod 700 "$bootstrap"

  set +e
  stdbuf -oL -eL bash "$bootstrap" --server "$SERVER_URL" --upgrade 2>&1 \
    | while IFS= read -r line; do
        printf '%s\n' "$line" | tee -a "$LOG_FILE"
        stage=""
        if [[ "$line" =~ ^\[([^]]+)\][[:space:]](.*)$ ]]; then
          stage="${BASH_REMATCH[1]}"
          message="${BASH_REMATCH[2]}"
          percent="$(stage_percent "$stage")"
          report running "$stage" "$percent" "$message"
        else
          now="$(date +%s)"
          last="$(cat "${STATE_DIR}/upgrade-last-report.epoch" 2>/dev/null || echo 0)"
          if (( now - last >= 2 )); then
            printf '%s\n' "$now" >"${STATE_DIR}/upgrade-last-report.epoch"
            current_stage="$(jq -r '.stage // "running"' "$STATE_FILE" 2>/dev/null || echo running)"
            current_percent="$(jq -r '.percent // 5' "$STATE_FILE" 2>/dev/null || echo 5)"
            report running "$current_stage" "$current_percent" "$line"
          fi
        fi
      done
  rc=${PIPESTATUS[0]}
  set -e

  rm -f "${STATE_DIR}/upgrade-last-report.epoch"
  if [[ "$rc" -ne 0 ]]; then
    tail_line="$(tail -n 1 "$LOG_FILE" 2>/dev/null || true)"
    report failed failed 100 "Worker 更新失败（exit=${rc}）${tail_line:+：$tail_line}"
    return "$rc"
  fi

  report succeeded complete 100 "Worker 已更新到中心服务器当前版本"
  log "online upgrade completed successfully"
  trap - EXIT
  rm -f "$bootstrap"
  return 0
}

if [[ "$EUID" -ne 0 ]]; then
  echo '{"ok":false,"error":"root_required"}'
  exit 1
fi

if ! load_identity; then
  echo '{"ok":false,"error":"worker_identity_invalid"}'
  exit 1
fi

case "$MODE" in
  --schedule)
    unit="chat2api-worker-upgrade-$(date +%s)-$$"
    if systemctl list-units --all --no-legend 'chat2api-worker-upgrade-*' 2>/dev/null | grep -q ' running '; then
      printf '{"ok":true,"scheduled":true,"already_running":true,"unit":"existing"}\n'
      exit 0
    fi
    systemd-run --quiet --collect --no-block --unit="$unit" /usr/local/sbin/chat2api-worker-upgrade --run
    printf '{"ok":true,"scheduled":true,"unit":"%s"}\n' "$unit"
    ;;
  --run)
    run_upgrade
    ;;
  *)
    echo '{"ok":false,"error":"unsupported_mode"}'
    exit 2
    ;;
esac
