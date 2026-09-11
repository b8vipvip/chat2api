#!/usr/bin/env bash
set -euo pipefail

WORKER_USER_HOME="${CHAT2API_CHROME_HOME:-/home/chat2api}"
PROFILE_DIR="${CHAT2API_CHROME_PROFILE:?CHAT2API_CHROME_PROFILE is required}"
EXTENSION_DIR="${CHAT2API_EXTENSION_DIR:-/opt/chat2api-worker/chrome_extension}"
SERVER_URL="${CHAT2API_SERVER_URL:-https://chat2api.mv3.cn}"
CHATGPT_URL="${CHATGPT_URL:-https://chatgpt.com/}"
PROXY_PORT="${CHAT2API_PROXY_PORT:?CHAT2API_PROXY_PORT is required}"
CDP_PORT="${CHAT2API_CDP_PORT:?CHAT2API_CDP_PORT is required}"
CHROME_BINARY="${CHAT2API_CHROME_BINARY:-${WORKER_USER_HOME}/.cache/chat2api-chrome-for-testing/chrome}"

log() {
  printf '%s [chat2api-slot-chrome] %s\n' "$(date -Is)" "$*"
}

[[ -x "$CHROME_BINARY" ]] || { log "Chrome for Testing is missing: $CHROME_BINARY"; exit 1; }
[[ -f "${EXTENSION_DIR}/manifest.json" ]] || { log "Chrome Bridge manifest missing: ${EXTENSION_DIR}/manifest.json"; exit 1; }
[[ "$PROXY_PORT" =~ ^[0-9]+$ && "$CDP_PORT" =~ ^[0-9]+$ ]] || { log "invalid slot ports"; exit 2; }

mkdir -p "$PROFILE_DIR"
chmod 700 "$PROFILE_DIR"

# Each slot owns a persistent profile. Clear only disposable restore/script
# caches so a restart cannot resurrect stale automation windows while cookies,
# Local Storage, IndexedDB, and the slot's ChatGPT login remain intact.
rm -rf "${PROFILE_DIR}/Default/Sessions" 2>/dev/null || true
rm -f \
  "${PROFILE_DIR}/Default/Current Session" \
  "${PROFILE_DIR}/Default/Current Tabs" \
  "${PROFILE_DIR}/Default/Last Session" \
  "${PROFILE_DIR}/Default/Last Tabs" 2>/dev/null || true
rm -rf \
  "${PROFILE_DIR}/Default/Service Worker/ScriptCache" \
  "${PROFILE_DIR}/Default/Code Cache/js" \
  "${PROFILE_DIR}/Default/Code Cache/wasm" 2>/dev/null || true

server_host="${SERVER_URL#*://}"
server_host="${server_host%%/*}"
server_host="${server_host%%:*}"
log "starting isolated Chrome profile=${PROFILE_DIR} socks=${PROXY_PORT} cdp=${CDP_PORT}"

exec "$CHROME_BINARY" \
  --user-data-dir="$PROFILE_DIR" \
  --password-store=basic \
  --proxy-server="socks5://127.0.0.1:${PROXY_PORT}" \
  "--proxy-bypass-list=localhost;127.0.0.1;${server_host}" \
  --disable-quic \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port="$CDP_PORT" \
  --disable-extensions-except="$EXTENSION_DIR" \
  --load-extension="$EXTENSION_DIR" \
  --no-first-run \
  --no-default-browser-check \
  --disable-dev-shm-usage \
  --window-position=0,0 \
  --window-size=1920,1080 \
  "$CHATGPT_URL"
