#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-}"
SLOT="${2:-}"
ENV_FILE="/etc/default/chat2api-worker-controller"
[[ -r "$ENV_FILE" ]] || { echo "controller environment missing" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"

[[ "$SLOT" =~ ^[0-9]+$ ]] || { echo "invalid slot" >&2; exit 2; }
(( SLOT >= 1 && SLOT <= 32 )) || { echo "slot out of range" >&2; exit 2; }
[[ "${WORKER_DIR:-}" == "/opt/chat2api-worker" ]] || { echo "invalid worker dir" >&2; exit 2; }
[[ "${WORKER_USER:-}" == "chat2api" ]] || { echo "invalid worker user" >&2; exit 2; }
[[ "${PROXY_PORT:-}" == "10808" ]] || { echo "invalid shared proxy port" >&2; exit 2; }

if (( SLOT == 1 )); then
  PROFILE_DIR="/home/chat2api/.config/chat2api-chrome-worker-01"
  DISPLAY_NUM=99
  CDP_PORT=9222
  XVFB_UNIT="chat2api-xvfb.service"
  CHROME_UNIT="chat2api-chrome.service"
else
  PROFILE_DIR="/home/chat2api/.config/chat2api-chrome-worker-$(printf '%02d' "$SLOT")"
  DISPLAY_NUM=$((98 + SLOT))
  CDP_PORT=$((9221 + SLOT))
  XVFB_UNIT="chat2api-xvfb-slot${SLOT}.service"
  CHROME_UNIT="chat2api-chrome-slot${SLOT}.service"
fi

provision() {
  (( SLOT >= 2 )) || { echo "slot 1 is provisioned by bootstrap" >&2; exit 2; }
  [[ -x /home/chat2api/.cache/chat2api-chrome-for-testing/chrome ]] || { echo "Chrome for Testing is not ready" >&2; exit 1; }
  [[ -f "$WORKER_DIR/scripts/linux_worker_slot_chrome_launcher.sh" ]] || { echo "slot Chrome launcher missing" >&2; exit 1; }
  if ss -ltnH "sport = :${CDP_PORT}" 2>/dev/null | grep -q . && ! systemctl is-active --quiet "$CHROME_UNIT"; then
    echo "CDP port ${CDP_PORT} already in use" >&2
    exit 1
  fi
  install -d -o chat2api -g chat2api -m 700 "$PROFILE_DIR"

  cat >"/etc/systemd/system/${XVFB_UNIT}" <<UNIT
[Service]
User=chat2api
ExecStart=/usr/bin/Xvfb :${DISPLAY_NUM} -screen 0 1920x1080x24 -nolisten tcp -ac
Restart=always
[Install]
WantedBy=multi-user.target
UNIT

  cat >"/etc/systemd/system/${CHROME_UNIT}" <<UNIT
[Unit]
Requires=chat2api-xray.service ${XVFB_UNIT}
After=chat2api-xray.service ${XVFB_UNIT}
[Service]
User=chat2api
Environment=HOME=/home/chat2api
Environment=DISPLAY=:${DISPLAY_NUM}
Environment=XDG_CONFIG_HOME=/home/chat2api/.config
Environment=XDG_CACHE_HOME=/home/chat2api/.cache
Environment=CHAT2API_CHROME_HOME=/home/chat2api
Environment=CHAT2API_CHROME_PROFILE=${PROFILE_DIR}
Environment=CHAT2API_EXTENSION_DIR=${WORKER_DIR}/chrome_extension
Environment=CHAT2API_PROXY_PORT=${PROXY_PORT}
Environment=CHAT2API_CDP_PORT=${CDP_PORT}
Environment=CHAT2API_SERVER_URL=${CHAT2API_SERVER_URL}
ExecStart=/bin/bash ${WORKER_DIR}/scripts/linux_worker_slot_chrome_launcher.sh
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT

  systemctl daemon-reload
  systemctl enable "$XVFB_UNIT" "$CHROME_UNIT" >/dev/null
  systemctl restart "$XVFB_UNIT"
  systemctl restart "$CHROME_UNIT"
  for _ in $(seq 1 60); do
    if systemctl is-active --quiet "$XVFB_UNIT" \
      && systemctl is-active --quiet "$CHROME_UNIT" \
      && ps -u chat2api -o args= | grep -F -- "--user-data-dir=${PROFILE_DIR}" | grep -E '[Cc]hrome' >/dev/null 2>&1; then
      printf '{"ok":true,"slot":%s,"profile":"%s","cdp_port":%s}\n' "$SLOT" "$PROFILE_DIR" "$CDP_PORT"
      return 0
    fi
    sleep 1
  done
  echo "Worker Chrome profile did not become ready" >&2
  return 1
}

initialize() {
  install -d -o chat2api -g chat2api -m 700 "$PROFILE_DIR"
  rm -rf "$PROFILE_DIR/Default/Service Worker" 2>/dev/null || true
  systemctl restart "$XVFB_UNIT"
  systemctl restart "$CHROME_UNIT"
  printf '{"ok":true,"slot":%s}\n' "$SLOT"
}

restart_chrome() {
  systemctl restart "$CHROME_UNIT"
  printf '{"ok":true,"slot":%s}\n' "$SLOT"
}

restart_xvfb() {
  systemctl restart "$XVFB_UNIT"
  printf '{"ok":true,"slot":%s}\n' "$SLOT"
}

diagnostics() {
  echo "=== chat2api device Worker diagnostics ==="
  echo "slot=${SLOT}"
  echo "profile=${PROFILE_DIR}"
  echo "display=:${DISPLAY_NUM}"
  echo "cdp_port=${CDP_PORT}"
  echo "shared_proxy_port=${PROXY_PORT}"
  echo "chrome_unit=${CHROME_UNIT}"
  echo "xvfb_unit=${XVFB_UNIT}"
  echo
  echo "=== service state ==="
  systemctl is-active chat2api-worker-agent.service || true
  systemctl is-active chat2api-xray.service || true
  systemctl is-active "$XVFB_UNIT" || true
  systemctl is-active "$CHROME_UNIT" || true
  echo
  echo "=== target unit logs ==="
  journalctl -u "$XVFB_UNIT" -u "$CHROME_UNIT" -n 120 --no-pager 2>&1 || true
  echo
  echo "=== controller logs ==="
  journalctl -u chat2api-worker-agent.service -n 120 --no-pager 2>&1 || true
  echo
  echo "=== Chrome target ==="
  curl -fsS --connect-timeout 2 --max-time 4 "http://127.0.0.1:${CDP_PORT}/json/version" 2>&1 || true
  echo
  echo "=== profile process ==="
  ps -u chat2api -o pid=,etimes=,args= | grep -F -- "--user-data-dir=${PROFILE_DIR}" | tail -20 || true
}

case "$ACTION" in
  provision) provision ;;
  initialize) initialize ;;
  restart-chrome) restart_chrome ;;
  restart-xvfb) restart_xvfb ;;
  diagnostics) diagnostics ;;
  *) echo "unsupported controller action" >&2; exit 2 ;;
esac
