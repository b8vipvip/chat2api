#!/usr/bin/env bash
set -euo pipefail

SERVER="https://chat2api.mv3.cn"
ENROLL_CODE=""
SLOT=""
ARGS=("$@")

while (($#)); do
  case "$1" in
    --server) SERVER="${2%/}"; shift 2;;
    --enroll-code) ENROLL_CODE="$2"; shift 2;;
    --slot) SLOT="$2"; shift 2;;
    *) echo "Unknown argument: $1" >&2; exit 2;;
  esac
done

[[ -n "$ENROLL_CODE" ]] || { echo "--enroll-code is required" >&2; exit 2; }
[[ "$SLOT" =~ ^[0-9]+$ ]] || { echo "--slot must be an integer" >&2; exit 2; }
(( SLOT >= 2 && SLOT <= 32 )) || { echo "--slot must be between 2 and 32" >&2; exit 2; }

report() {
  local state="$1" stage="$2" message="$3"
  local payload
  payload="$(python3 - "$ENROLL_CODE" "$state" "$stage" "$message" <<'PY'
import json, sys
print(json.dumps({
    "enroll_code": sys.argv[1],
    "state": sys.argv[2],
    "stage": sys.argv[3],
    "message": sys.argv[4],
}, ensure_ascii=False))
PY
)"
  curl -fsS --max-time 8 -H 'Content-Type: application/json' --data-binary "$payload" "$SERVER/api/workers/install-progress" >/dev/null 2>&1 || true
}

failed=1
finish() {
  local rc=$?
  if (( failed == 1 )); then
    report "failed" "worker-slot" "Worker ${SLOT} 安装失败（exit=${rc}）"
  fi
  exit "$rc"
}
trap finish EXIT

report "installing" "worker-slot" "开始安装同机隔离 Worker ${SLOT}"

SCRIPT="/opt/chat2api-worker/scripts/linux_worker_slot_install.sh"
[[ -x "$SCRIPT" || -f "$SCRIPT" ]] || { echo "Worker slot installer is missing: $SCRIPT" >&2; exit 1; }

bash "$SCRIPT" "${ARGS[@]}"
report "installed" "complete" "Worker ${SLOT} 已安装并启动；现在可在控制台单独配置代理、登录 ChatGPT 和配对"
failed=0
trap - EXIT

echo "Worker ${SLOT} installation reported to ${SERVER}."
