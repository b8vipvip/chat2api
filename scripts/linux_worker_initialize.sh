#!/usr/bin/env bash
set -Eeuo pipefail

MODE="${1:---schedule}"
PROFILE_DIR="${CHAT2API_CHROME_PROFILE:-/home/chat2api/.config/chat2api-chrome-worker-01}"
DEBUG_URL="${CHAT2API_CHROME_DEBUG_URL:-http://127.0.0.1:9222}"
CHATGPT_URL="${CHATGPT_URL:-https://chatgpt.com/}"
TAB_INIT_HELPER="${CHAT2API_TAB_INIT_HELPER:-/opt/chat2api-worker/scripts/linux_worker_tab_init.py}"
LOCK_FILE="/run/chat2api-worker-initialize.lock"
STATE_FILE="/var/lib/chat2api-worker/initialize-state.json"
UNITS=(chat2api-xray.service chat2api-xvfb.service chat2api-chrome.service chat2api-worker-agent.service)

log() {
  printf '%s [chat2api-worker-initialize] %s\n' "$(date -Is)" "$*"
}

write_state() {
  local status="$1" stage="$2" message="$3"
  install -d -m 0755 /var/lib/chat2api-worker
  python3 - "$STATE_FILE" "$status" "$stage" "$message" <<'PY'
from __future__ import annotations
import json, os, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
path = Path(sys.argv[1])
status, stage, message = sys.argv[2:5]
now = datetime.now(timezone.utc).isoformat()
try:
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except Exception:
    old = {}
payload = {
    "status": status,
    "stage": stage,
    "message": message,
    "started_at": old.get("started_at") if status not in {"starting"} else now,
    "updated_at": now,
    "completed_at": now if status in {"succeeded", "failed"} else "",
}
if not payload["started_at"]:
    payload["started_at"] = now
fd, tmp = tempfile.mkstemp(prefix=".initialize-state.", dir=str(path.parent), text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush(); os.fsync(handle.fileno())
    os.replace(tmp, path)
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)
PY
}

wait_active() {
  local unit="$1" attempts="${2:-45}"
  local i
  for ((i=0; i<attempts; i++)); do
    systemctl is-active --quiet "$unit" && return 0
    sleep 1
  done
  return 1
}

wait_cdp() {
  local i
  for i in $(seq 1 60); do
    curl -fsS --connect-timeout 1 --max-time 2 "${DEBUG_URL}/json/version" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

extension_runtime_ready() {
  python3 - "$DEBUG_URL" <<'PY'
from __future__ import annotations
import json, sys
from urllib.request import urlopen
try:
    with urlopen(sys.argv[1].rstrip('/') + '/json/list', timeout=2.5) as response:
        targets = json.loads(response.read(1_000_000).decode('utf-8'))
except Exception:
    raise SystemExit(1)
for item in targets if isinstance(targets, list) else []:
    if not isinstance(item, dict):
        continue
    if item.get('type') == 'service_worker' and str(item.get('url') or '').startswith('chrome-extension://'):
        raise SystemExit(0)
raise SystemExit(1)
PY
}

wait_extension_runtime() {
  local i
  for i in $(seq 1 45); do
    extension_runtime_ready && return 0
    sleep 1
  done
  return 1
}

ensure_one_chatgpt_page() {
  python3 - "$DEBUG_URL" "$CHATGPT_URL" <<'PY'
from __future__ import annotations
import json, sys
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
base = sys.argv[1].rstrip('/')
target_url = sys.argv[2]
hosts = {'chatgpt.com','www.chatgpt.com','chat.openai.com'}
try:
    with urlopen(base + '/json/list', timeout=2.5) as response:
        targets = json.loads(response.read(1_000_000).decode('utf-8'))
except Exception:
    raise SystemExit(1)
pages = []
for item in targets if isinstance(targets, list) else []:
    if not isinstance(item, dict) or item.get('type') != 'page':
        continue
    try:
        if urlsplit(str(item.get('url') or '')).hostname in hosts:
            pages.append(item)
    except ValueError:
        pass
if not pages:
    request = Request(base + '/json/new?' + quote(target_url, safe=':/'), data=b'', method='PUT')
    with urlopen(request, timeout=3) as response:
        response.read(64_000)
PY
  if [[ -f "$TAB_INIT_HELPER" ]]; then
    python3 "$TAB_INIT_HELPER" --debug-url "$DEBUG_URL" --keep 1 --wait 12 || true
  fi
}

run_initialize() {
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    log "another initialization is already running"
    exit 0
  fi

  local failed=0
  cleanup() {
    # Keep the control plane recoverable even when Chrome initialization fails.
    systemctl start chat2api-worker-agent.service >/dev/null 2>&1 || true
  }
  trap cleanup EXIT

  write_state starting starting "正在初始化 Linux Worker"
  log "stopping Agent and Chrome before a clean browser initialization"
  write_state running stop "停止 Agent 与 Chrome"
  systemctl stop chat2api-worker-agent.service chat2api-chrome.service || true

  write_state running services "重启 Xray 与 Xvfb"
  systemctl restart chat2api-xray.service
  wait_active chat2api-xray.service 30 || { log "Xray failed to become active"; failed=1; }
  systemctl restart chat2api-xvfb.service
  wait_active chat2api-xvfb.service 30 || { log "Xvfb failed to become active"; failed=1; }

  # A source update under the same unpacked-extension ID can leave Chromium's
  # persisted ServiceWorker registration in a failed state even after ScriptCache
  # is cleared. This directory is disposable extension runtime state; ChatGPT
  # Cookies, Local Storage and IndexedDB are intentionally left untouched.
  write_state running bridge_reset "重置 Chrome Bridge Service Worker 运行态"
  rm -rf "${PROFILE_DIR}/Default/Service Worker" 2>/dev/null || true

  write_state running chrome "启动 Chrome 并打开一个 ChatGPT 窗口"
  systemctl start chat2api-chrome.service
  wait_active chat2api-chrome.service 45 || { log "Chrome service failed to become active"; failed=1; }
  if [[ "$failed" -eq 0 ]] && wait_cdp; then
    ensure_one_chatgpt_page || true
    if ! wait_extension_runtime; then
      log "Chrome Bridge Service Worker did not start; retrying one clean browser cycle"
      systemctl stop chat2api-chrome.service || true
      rm -rf "${PROFILE_DIR}/Default/Service Worker" 2>/dev/null || true
      systemctl start chat2api-chrome.service
      wait_active chat2api-chrome.service 45 || failed=1
      if [[ "$failed" -eq 0 ]] && wait_cdp; then
        ensure_one_chatgpt_page || true
        wait_extension_runtime || failed=1
      else
        failed=1
      fi
    fi
  else
    log "Chrome CDP did not become ready"
    failed=1
  fi

  write_state running agent "启动 Worker Agent"
  systemctl restart chat2api-worker-agent.service
  wait_active chat2api-worker-agent.service 30 || failed=1

  if [[ "$failed" -ne 0 ]]; then
    write_state failed failed "Worker 初始化未完全成功，请下载诊断日志"
    log "initialization finished with failures"
    return 1
  fi

  write_state succeeded completed "Worker 初始化完成：Agent、浏览器已重启，ChatGPT 初始化窗口已打开"
  log "initialization completed successfully"
  trap - EXIT
  return 0
}

if [[ "$EUID" -ne 0 ]]; then
  echo '{"ok":false,"error":"root_required"}'
  exit 1
fi

case "$MODE" in
  --schedule)
    unit="chat2api-worker-initialize-$(date +%s)-$$"
    systemd-run --quiet --collect --no-block --unit="$unit" /usr/local/sbin/chat2api-worker-initialize --run
    printf '{"ok":true,"scheduled":true,"unit":"%s"}\n' "$unit"
    ;;
  --run)
    run_initialize
    ;;
  *)
    echo '{"ok":false,"error":"unsupported_mode"}'
    exit 2
    ;;
esac
