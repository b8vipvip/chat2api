#!/usr/bin/env bash
set -euo pipefail

WORKER_USER_HOME="${CHAT2API_CHROME_HOME:-/home/chat2api}"
PROFILE_DIR="${CHAT2API_CHROME_PROFILE:-${WORKER_USER_HOME}/.config/chat2api-chrome-worker-01}"
EXTENSION_DIR="${CHAT2API_EXTENSION_DIR:-/opt/chat2api-worker/chrome_extension}"
SERVER_URL="${CHAT2API_SERVER_URL:-https://chat2api.mv3.cn}"
PROXY_PORT="${CHAT2API_PROXY_PORT:-10808}"
CACHE_ROOT="${CHAT2API_CFT_CACHE_DIR:-${WORKER_USER_HOME}/.cache/chat2api-chrome-for-testing}"
META_URL="${CHAT2API_CFT_META_URL:-https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json}"
CURRENT_LINK="${CACHE_ROOT}/chrome"

log() {
  printf '%s [chat2api-cft] %s\n' "$(date -Is)" "$*"
}

download() {
  local url="$1" output="$2"
  # Xray is required by the Chrome unit and its local SOCKS listener exists even
  # before an external proxy is configured (the initial outbound is freedom).
  # Prefer that single network path, but retain a direct fallback for installer
  # recovery if the local listener is momentarily unavailable.
  if curl -fSL --retry 5 --retry-delay 2 --retry-all-errors --connect-timeout 15 --max-time 600 \
      --proxy "socks5h://127.0.0.1:${PROXY_PORT}" -o "$output" "$url"; then
    return 0
  fi
  log "SOCKS download path unavailable; retrying Chrome for Testing download directly"
  curl -fSL --retry 5 --retry-delay 2 --retry-all-errors --connect-timeout 15 --max-time 600 \
    -o "$output" "$url"
}

[[ -f "${EXTENSION_DIR}/manifest.json" ]] || { log "Chrome Bridge manifest missing: ${EXTENSION_DIR}/manifest.json"; exit 1; }
command -v curl >/dev/null
command -v jq >/dev/null
command -v unzip >/dev/null

mkdir -p "$CACHE_ROOT" "$PROFILE_DIR"
chmod 700 "$CACHE_ROOT" "$PROFILE_DIR"

meta="$(mktemp "${CACHE_ROOT}/metadata.XXXXXX.json")"
archive=""
staging=""
cleanup() {
  rm -f "$meta"
  [[ -n "$archive" ]] && rm -f "$archive"
  [[ -n "$staging" ]] && rm -rf "$staging"
}
trap cleanup EXIT

download "$META_URL" "$meta"
version="$(jq -er '.channels.Stable.version' "$meta")"
archive_url="$(jq -er '.channels.Stable.downloads.chrome[] | select(.platform == "linux64") | .url' "$meta" | head -n1)"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { log "invalid Chrome for Testing version: ${version}"; exit 1; }
[[ "$archive_url" == https://storage.googleapis.com/chrome-for-testing-public/* ]] || { log "unexpected Chrome for Testing download URL"; exit 1; }

target="${CACHE_ROOT}/${version}"
binary="${target}/chrome-linux64/chrome"
if [[ ! -x "$binary" ]]; then
  log "installing Chrome for Testing Stable ${version} for automated extension loading"
  archive="$(mktemp "${CACHE_ROOT}/chrome.XXXXXX.zip")"
  staging="$(mktemp -d "${CACHE_ROOT}/install.XXXXXX")"
  download "$archive_url" "$archive"
  unzip -q "$archive" -d "$staging"
  [[ -x "${staging}/chrome-linux64/chrome" ]] || { log "downloaded Chrome for Testing archive is incomplete"; exit 1; }
  rm -rf "${target}.new"
  mv "$staging" "${target}.new"
  staging=""
  rm -rf "$target"
  mv "${target}.new" "$target"
fi

ln -sfn "$binary" "$CURRENT_LINK"
# Keep one previous Stable build for rollback and bound disk growth.
mapfile -t old_versions < <(find "$CACHE_ROOT" -mindepth 1 -maxdepth 1 -type d -name '[0-9]*' -printf '%f\n' | sort -V -r | tail -n +3)
for old in "${old_versions[@]:-}"; do
  [[ -n "$old" ]] && rm -rf "${CACHE_ROOT}/${old}"
done

server_host="${SERVER_URL#*://}"
server_host="${server_host%%/*}"
server_host="${server_host%%:*}"
log "starting Chrome for Testing ${version}; Chrome Bridge=${EXTENSION_DIR}"
exec "$CURRENT_LINK" \
  --user-data-dir="$PROFILE_DIR" \
  --password-store=basic \
  --proxy-server="socks5://127.0.0.1:${PROXY_PORT}" \
  "--proxy-bypass-list=localhost;127.0.0.1;${server_host}" \
  --load-extension="$EXTENSION_DIR" \
  --no-first-run \
  --no-default-browser-check \
  --disable-dev-shm-usage \
  --window-position=0,0 \
  --window-size=1920,1080 \
  about:blank
