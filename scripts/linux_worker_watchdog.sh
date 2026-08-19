#!/usr/bin/env bash
set -euo pipefail

WORKER_USER="${WORKER_USER:-chat2api}"
PROFILE_DIR="${PROFILE_DIR:-/home/${WORKER_USER}/.config/chat2api-chrome-worker-01}"
EXTENSION_DIR="${EXTENSION_DIR:-/opt/chat2api/chrome_extension}"
PROXY_PORT="${PROXY_PORT:-10808}"
CHATGPT_URL="${CHATGPT_URL:-https://chatgpt.com/}"
CHAT2API_SERVER_URL="${CHAT2API_SERVER_URL:-https://chat2api.mv3.cn}"
XRAY_UNIT="${XRAY_UNIT:-chat2api-xray.service}"
XVFB_UNIT="${XVFB_UNIT:-chat2api-xvfb.service}"
CHROME_UNIT="${CHROME_UNIT:-chat2api-chrome.service}"

log() {
  local level="$1"
  shift
  local message="$*"
  printf '%s [%s] %s\n' "$(date -Is)" "${level}" "${message}"
  logger -t chat2api-linux-worker-watchdog -- "[${level}] ${message}" 2>/dev/null || true
}

unit_active() {
  systemctl is-active --quiet "$1"
}

restart_unit() {
  local unit="$1"
  local reason="$2"
  log WARN "${unit} requires recovery: ${reason}; restarting it once"
  systemctl restart "${unit}"
}

proxy_listener_ready() {
  (echo >/dev/tcp/127.0.0.1/"${PROXY_PORT}") >/dev/null 2>&1
}

wait_for_proxy_listener() {
  local attempts="${1:-10}"
  local delay="${2:-1}"
  local i
  for ((i=0; i<attempts; i++)); do
    if proxy_listener_ready; then
      return 0
    fi
    sleep "${delay}"
  done
  return 1
}

chrome_process_ready() {
  # Chrome for Testing uses a different executable/process name from branded
  # Google Chrome. The dedicated Worker profile is the stable identity marker.
  ps -u "${WORKER_USER}" -o args= 2>/dev/null \
    | grep -F -- "--user-data-dir=${PROFILE_DIR}" \
    | grep -E '[Cc]hrome' >/dev/null 2>&1
}

chatgpt_transport_ready() {
  # Deliberately do not use --fail: an HTTP 4xx/5xx still proves that the
  # browser worker's SOCKS path can resolve and establish an HTTPS connection.
  curl --silent --show-error --output /dev/null \
    --connect-timeout 8 --max-time 20 \
    --proxy "socks5h://127.0.0.1:${PROXY_PORT}" \
    "${CHATGPT_URL}"
}

server_health_ready() {
  curl --fail --silent --show-error --output /dev/null \
    --connect-timeout 5 --max-time 10 \
    "${CHAT2API_SERVER_URL%/}/healthz"
}

if [[ "${EUID}" -ne 0 ]]; then
  log ERROR "run as root so failed systemd units can be recovered safely"
  exit 1
fi

if ! id "${WORKER_USER}" >/dev/null 2>&1; then
  log ERROR "worker user does not exist: ${WORKER_USER}"
  exit 1
fi

# Never recreate a missing or mis-owned production profile automatically. Doing
# so could silently produce a fresh extension identity and lose the known
# ChatGPT session. Surface the condition for manual repair instead.
if [[ ! -d "${PROFILE_DIR}" ]]; then
  log ERROR "persistent Chrome profile is missing; refusing automatic recovery: ${PROFILE_DIR}"
  exit 1
fi
profile_owner="$(stat -c '%U' "${PROFILE_DIR}" 2>/dev/null || true)"
if [[ "${profile_owner}" != "${WORKER_USER}" ]]; then
  log ERROR "persistent Chrome profile owner is ${profile_owner:-unknown}, expected ${WORKER_USER}; refusing automatic recovery"
  exit 1
fi

# An unpacked extension is path-backed. A Chrome restart while the repository
# path is missing would bring the browser back without a usable bridge.
if [[ ! -f "${EXTENSION_DIR}/manifest.json" ]]; then
  log ERROR "Chrome Bridge source is missing; refusing automatic Chrome restart: ${EXTENSION_DIR}/manifest.json"
  exit 1
fi

if ! unit_active "${XRAY_UNIT}"; then
  restart_unit "${XRAY_UNIT}" "systemd unit is not active"
fi
if ! wait_for_proxy_listener 10 1; then
  # Xray can occasionally remain active while its inbound listener is absent.
  # This is a definitive local failure, so one restart is safe. External route
  # failures below do not trigger restart loops.
  restart_unit "${XRAY_UNIT}" "127.0.0.1:${PROXY_PORT} is not listening"
  if ! wait_for_proxy_listener 15 1; then
    log ERROR "proxy listener did not recover on 127.0.0.1:${PROXY_PORT}"
    exit 1
  fi
fi

if ! unit_active "${XVFB_UNIT}"; then
  restart_unit "${XVFB_UNIT}" "systemd unit is not active"
  sleep 1
fi
if ! unit_active "${XVFB_UNIT}"; then
  log ERROR "virtual display did not recover: ${XVFB_UNIT}"
  exit 1
fi

if ! unit_active "${CHROME_UNIT}" || ! chrome_process_ready; then
  restart_unit "${CHROME_UNIT}" "Chrome service/process is not healthy"
  sleep 5
fi
if ! unit_active "${CHROME_UNIT}" || ! chrome_process_ready; then
  log ERROR "production Chrome worker did not recover: ${CHROME_UNIT}"
  exit 1
fi

# Connectivity failures are observable but are not blindly repaired by process
# restarts. They may represent an upstream node, DNS, ChatGPT, or server outage;
# repeatedly restarting healthy local services would only create a restart loop.
degraded=0
if ! chatgpt_transport_ready; then
  log ERROR "ChatGPT is not reachable through socks5h://127.0.0.1:${PROXY_PORT}; local services were left running"
  degraded=1
fi
if ! server_health_ready; then
  log ERROR "chat2api server health endpoint is not reachable: ${CHAT2API_SERVER_URL%/}/healthz; local services were left running"
  degraded=1
fi

if (( degraded )); then
  exit 1
fi

log INFO "healthy: Xray listener, Xvfb, persistent-profile Chrome, ChatGPT proxy path and chat2api server are reachable"
