#!/usr/bin/env bash
set -Eeuo pipefail

BASE_SCRIPT="${CHAT2API_BASE_AUTORELOAD:-/opt/chat2api-worker/scripts/linux_extension_autoreload.sh}"
PROFILE_DIR="${PROFILE_DIR:-/home/chat2api/.config/chat2api-chrome-worker-01}"
CHROME_UNIT="${CHROME_UNIT:-chat2api-chrome.service}"
DEBUG_URL="${CHAT2API_CHROME_DEBUG_URL:-http://127.0.0.1:9222}"
APPLIED_FILE="${STATE_DIR:-/var/lib/chat2api-worker}/extension-applied.sha256"
FAILED_RUNTIME_FILE="${STATE_DIR:-/var/lib/chat2api-worker}/extension-runtime-failed.txt"

log() {
  local level="$1"; shift
  printf '%s [%s] %s\n' "$(date -Is)" "$level" "$*"
  logger -t chat2api-linux-extension-autoreload -- "[$level] $*" 2>/dev/null || true
}

runtime_ready() {
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
    if isinstance(item, dict) and item.get('type') == 'service_worker' and str(item.get('url') or '').startswith('chrome-extension://'):
        raise SystemExit(0)
raise SystemExit(1)
PY
}

wait_runtime() {
  local attempts="${1:-30}" i
  for ((i=0; i<attempts; i++)); do
    runtime_ready && return 0
    sleep 1
  done
  return 1
}

[[ "$EUID" -eq 0 ]] || { log ERROR "run as root"; exit 1; }
[[ -x "$BASE_SCRIPT" ]] || { log ERROR "base autoreload script is missing: $BASE_SCRIPT"; exit 1; }

"$BASE_SCRIPT"

# A healthy Bridge keeps an authenticated WebSocket open, so its MV3 service
# worker should be observable through loopback CDP. A running Chrome process by
# itself is not enough: Chromium can repeatedly fail DidStartWorker while the
# service unit remains green.
if wait_runtime 30; then
  rm -f "$FAILED_RUNTIME_FILE"
  exit 0
fi

log WARN "Chrome is running but the Bridge Service Worker is unavailable; resetting disposable Service Worker state once"
systemctl stop "$CHROME_UNIT" || true
rm -rf "${PROFILE_DIR}/Default/Service Worker" 2>/dev/null || true
systemctl start "$CHROME_UNIT"

for _ in $(seq 1 45); do
  if systemctl is-active --quiet "$CHROME_UNIT" && wait_runtime 1; then
    rm -f "$FAILED_RUNTIME_FILE"
    log INFO "Chrome Bridge runtime recovered after Service Worker state reset"
    exit 0
  fi
  sleep 1
done

# Do not leave the source fingerprint recorded as healthy. The next timer pass
# is allowed to retry instead of permanently accepting a broken runtime.
rm -f "$APPLIED_FILE"
printf '%s\n' "$(date -Is) Bridge Service Worker unavailable after clean restart" >"$FAILED_RUNTIME_FILE"
log ERROR "Chrome Bridge runtime is still unavailable after a clean restart"
exit 1
