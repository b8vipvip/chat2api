#!/usr/bin/env bash
set -euo pipefail

# Root-only, fixed-scope diagnostic collector for the Linux Worker Agent.
# It accepts no arguments so a compromised unprivileged Agent cannot turn it
# into an arbitrary journal reader or command runner.

UNITS=(
  chat2api-worker-agent.service
  chat2api-chrome.service
  chat2api-xray.service
  chat2api-xvfb.service
  chat2api-worker-watchdog.service
  chat2api-extension-autoreload.service
)
EXTENSION_DIR=/opt/chat2api-worker/chrome_extension
STATE_DIR=/var/lib/chat2api-worker

redact() {
  sed -E \
    -e 's/wbind_[A-Za-z0-9._-]+/wbind_[REDACTED]/g' \
    -e 's/(worker_token["=: ]+)[A-Za-z0-9._-]+/\1[REDACTED]/Ig' \
    -e 's/(authorization:[[:space:]]*(bearer|basic)[[:space:]]+)[A-Za-z0-9+\/_=.-]+/\1[REDACTED]/Ig' \
    -e 's/([?&](token|code|key)=)[^&[:space:]]+/\1[REDACTED]/Ig'
}

printf 'chat2api Linux Worker diagnostic bundle\n'
printf 'generated_at=%s\n' "$(date -Is)"
printf 'hostname=%s\n' "$(hostname 2>/dev/null || true)"
printf 'kernel=%s\n' "$(uname -srmo 2>/dev/null || true)"
printf '\n===== service state =====\n'
for unit in "${UNITS[@]}" chat2api-worker-watchdog.timer chat2api-extension-autoreload.timer; do
  printf '%s: ' "$unit"
  systemctl is-active "$unit" 2>/dev/null || true
done

printf '\n===== service properties =====\n'
for unit in chat2api-worker-agent.service chat2api-chrome.service chat2api-xray.service chat2api-xvfb.service; do
  printf '\n--- %s ---\n' "$unit"
  systemctl show "$unit" \
    -p ActiveState -p SubState -p MainPID -p ExecMainPID -p ExecMainStatus \
    -p NRestarts -p ActiveEnterTimestamp -p FragmentPath --no-pager 2>/dev/null || true
done

printf '\n===== browser / extension =====\n'
if [[ -L /home/chat2api/.cache/chat2api-chrome-for-testing/chrome ]]; then
  printf 'chrome_link=%s\n' "$(readlink -f /home/chat2api/.cache/chat2api-chrome-for-testing/chrome 2>/dev/null || true)"
fi
if [[ -r "${EXTENSION_DIR}/manifest.json" ]]; then
  printf 'extension_version=%s\n' "$(jq -r '.version // ""' "${EXTENSION_DIR}/manifest.json" 2>/dev/null || true)"
fi
printf 'capacity_control_v35_source=%s\n' "$([[ -r "${EXTENSION_DIR}/background_capacity_control_v35.js" ]] && echo present || echo missing)"
printf 'capacity_control_v36_source=%s\n' "$([[ -r "${EXTENSION_DIR}/background_capacity_control_v36.js" ]] && echo present || echo missing)"
printf 'capacity_control_v35_loaded_by_entry=%s\n' "$([[ -r "${EXTENSION_DIR}/background_entry.js" ]] && grep -q 'background_capacity_control_v35.js' "${EXTENSION_DIR}/background_entry.js" && echo yes || echo no)"
printf 'capacity_control_v36_loaded_by_entry=%s\n' "$([[ -r "${EXTENSION_DIR}/background_entry.js" ]] && grep -q 'background_capacity_control_v36.js' "${EXTENSION_DIR}/background_entry.js" && echo yes || echo no)"
if [[ -r "${STATE_DIR}/extension-state.env" ]]; then
  printf '%s\n' '--- extension-state.env ---'
  grep -E '^(EXTENSION_VERSION|EXTENSION_FINGERPRINT|GIT_COMMIT|CENTRAL_BUNDLE_SHA256|APPLIED_AT)=' "${STATE_DIR}/extension-state.env" 2>/dev/null || true
fi
for marker in extension-applied.sha256 extension-failed.sha256 extension-central-bundle.sha256; do
  if [[ -r "${STATE_DIR}/${marker}" ]]; then
    printf '%s=%s\n' "${marker}" "$(head -c 80 "${STATE_DIR}/${marker}" 2>/dev/null || true)"
  fi
done
printf 'chrome_processes=%s\n' "$(pgrep -u chat2api -fc 'chrome' 2>/dev/null || true)"
printf 'cdp_9222='; ss -lnt 2>/dev/null | awk '$4 ~ /127\.0\.0\.1:9222$/ {found=1} END {print found ? "listening" : "not_listening"}'

printf '\n===== recent journal (last 30 minutes, max 220 lines per unit) =====\n'
for unit in "${UNITS[@]}"; do
  printf '\n--- %s ---\n' "$unit"
  journalctl -u "$unit" --since '-30 min' -n 220 --no-pager -o short-iso 2>&1 || true
done | redact
