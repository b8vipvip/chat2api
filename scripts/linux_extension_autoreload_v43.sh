#!/usr/bin/env bash
set -Eeuo pipefail

BASE_SCRIPT="${CHAT2API_BASE_AUTORELOAD:-/opt/chat2api-worker/scripts/linux_extension_autoreload.sh}"
PROFILE_DIR="${PROFILE_DIR:-/home/chat2api/.config/chat2api-chrome-worker-01}"
CHROME_UNIT="${CHROME_UNIT:-chat2api-chrome.service}"
DEBUG_URL="${CHAT2API_CHROME_DEBUG_URL:-http://127.0.0.1:9222}"
SERVER_URL="${CHAT2API_SERVER_URL:-}"
STATE_DIR="${STATE_DIR:-/var/lib/chat2api-worker}"
APPLIED_FILE="${STATE_DIR}/extension-applied.sha256"
FAILED_RUNTIME_FILE="${STATE_DIR}/extension-runtime-failed.txt"

log() {
  local level="$1"; shift
  printf '%s [%s] %s\n' "$(date -Is)" "$level" "$*"
  logger -t chat2api-linux-extension-autoreload -- "[$level] $*" 2>/dev/null || true
}

repair_base_script() {
  [[ -n "$SERVER_URL" ]] || return 1
  command -v curl >/dev/null 2>&1 || return 1
  command -v tar >/dev/null 2>&1 || return 1
  command -v sha256sum >/dev/null 2>&1 || return 1
  local meta bundle tmp expected
  meta="$(mktemp)"; bundle="$(mktemp)"; tmp="$(mktemp -d)"
  cleanup_repair() { rm -f "$meta" "$bundle"; rm -rf "$tmp"; }
  if ! curl -fsS --connect-timeout 5 --max-time 15 -o "$meta" "${SERVER_URL%/}/bootstrap/linux-worker-bundle.json"; then cleanup_repair; return 1; fi
  expected="$(python3 - "$meta" <<'PY'
import json,sys
try:
    d=str(json.load(open(sys.argv[1],encoding='utf-8')).get('sha256') or '').strip().lower()
    print(d if len(d)==64 and all(c in '0123456789abcdef' for c in d) else '')
except Exception:
    print('')
PY
)"
  [[ -n "$expected" ]] || { cleanup_repair; return 1; }
  if ! curl -fSL --retry 2 --retry-delay 1 --retry-all-errors --connect-timeout 8 --max-time 120 -o "$bundle" "${SERVER_URL%/}/bootstrap/linux-worker-bundle.tar.gz"; then cleanup_repair; return 1; fi
  if ! printf '%s  %s\n' "$expected" "$bundle" | sha256sum -c - >/dev/null 2>&1; then cleanup_repair; return 1; fi
  if ! tar -xzf "$bundle" -C "$tmp" scripts/linux_extension_autoreload.sh >/dev/null 2>&1; then cleanup_repair; return 1; fi
  [[ -s "$tmp/scripts/linux_extension_autoreload.sh" ]] || { cleanup_repair; return 1; }
  install -o root -g root -m 755 "$tmp/scripts/linux_extension_autoreload.sh" "$BASE_SCRIPT"
  cleanup_repair
  log INFO "restored missing base autoreload helper from verified central Worker Bundle"
  return 0
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
if [[ ! -x "$BASE_SCRIPT" ]]; then
  log WARN "base autoreload script is missing: $BASE_SCRIPT; attempting verified self-repair"
  repair_base_script || { log ERROR "unable to restore base autoreload script from central Worker Bundle"; exit 1; }
fi

"$BASE_SCRIPT"

if wait_runtime 30; then
  rm -f "$FAILED_RUNTIME_FILE"
  exit 0
fi

log WARN "Chrome is running but the Worker Service Worker is unavailable; resetting disposable Service Worker state once"
systemctl stop "$CHROME_UNIT" || true
rm -rf "${PROFILE_DIR}/Default/Service Worker" 2>/dev/null || true
systemctl start "$CHROME_UNIT"

for _ in $(seq 1 45); do
  if systemctl is-active --quiet "$CHROME_UNIT" && wait_runtime 1; then
    rm -f "$FAILED_RUNTIME_FILE"
    log INFO "Worker runtime recovered after Service Worker state reset"
    exit 0
  fi
  sleep 1
done

rm -f "$APPLIED_FILE"
printf '%s\n' "$(date -Is) Worker Service Worker unavailable after clean restart" >"$FAILED_RUNTIME_FILE"
log ERROR "Worker runtime is still unavailable after a clean restart"
exit 1
