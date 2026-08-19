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
if [[ -r /opt/chat2api-worker/chrome_extension/manifest.json ]]; then
  printf 'extension_version=%s\n' "$(jq -r '.version // ""' /opt/chat2api-worker/chrome_extension/manifest.json 2>/dev/null || true)"
fi
printf 'chrome_processes=%s\n' "$(pgrep -u chat2api -fc 'chrome' 2>/dev/null || true)"
printf 'cdp_9222='; ss -lnt 2>/dev/null | awk '$4 ~ /127\.0\.0\.1:9222$/ {found=1} END {print found ? "listening" : "not_listening"}'

printf '\n===== recent journal (last 30 minutes, max 220 lines per unit) =====\n'
for unit in "${UNITS[@]}"; do
  printf '\n--- %s ---\n' "$unit"
  journalctl -u "$unit" --since '-30 min' -n 220 --no-pager -o short-iso 2>&1 || true
done | redact
