#!/usr/bin/env bash
set -euo pipefail

SERVER="https://chat2api.mv3.cn"
ENROLL_CODE=""
SLOT=""
WORKER_DIR="/opt/chat2api-worker"
VENV_DIR="/opt/chat2api-worker-venv"
WORKER_USER="chat2api"

while (($#)); do
  case "$1" in
    --server) SERVER="${2%/}"; shift 2;;
    --enroll-code) ENROLL_CODE="$2"; shift 2;;
    --slot) SLOT="$2"; shift 2;;
    *) echo "Unknown argument: $1" >&2; exit 2;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "Run with sudo/root" >&2; exit 1; }
[[ "$SLOT" =~ ^[0-9]+$ ]] || { echo "--slot must be an integer between 2 and 32" >&2; exit 2; }
(( SLOT >= 2 && SLOT <= 32 )) || { echo "--slot must be between 2 and 32" >&2; exit 2; }
[[ -n "$ENROLL_CODE" ]] || { echo "--enroll-code is required" >&2; exit 2; }
[[ "$SERVER" == https://* || "${CHAT2API_ALLOW_INSECURE_HTTP:-0}" == "1" ]] || { echo "Server must use HTTPS" >&2; exit 2; }
[[ -d "$WORKER_DIR/chrome_extension" && -f "$WORKER_DIR/scripts/linux_worker_agent.py" ]] || {
  echo "Primary chat2api Worker must be installed/upgraded before adding another slot" >&2; exit 1;
}
[[ -x "$VENV_DIR/bin/python" ]] || { echo "Primary Worker Python environment is missing" >&2; exit 1; }
[[ -x /home/chat2api/.cache/chat2api-chrome-for-testing/chrome ]] || { echo "Primary Worker Chrome for Testing is not ready" >&2; exit 1; }

INSTANCE="slot${SLOT}"
CONFIG_DIR="/etc/chat2api-worker/${INSTANCE}"
STATE_DIR="/var/lib/chat2api-worker/${INSTANCE}"
PROFILE_DIR="/home/chat2api/.config/chat2api-chrome-worker-$(printf '%02d' "$SLOT")"
PROXY_PORT=$((10807 + SLOT))
DISPLAY_NUM=$((98 + SLOT))
CDP_PORT=$((9221 + SLOT))
XRAY_UNIT="chat2api-xray-${INSTANCE}.service"
XVFB_UNIT="chat2api-xvfb-${INSTANCE}.service"
CHROME_UNIT="chat2api-chrome-${INSTANCE}.service"
AGENT_UNIT="chat2api-worker-agent-${INSTANCE}.service"
WATCHDOG_UNIT="chat2api-worker-watchdog-${INSTANCE}"
AUTORELOAD_UNIT="chat2api-extension-autoreload-${INSTANCE}"
PROXY_HELPER="/usr/local/sbin/chat2api-worker-proxy-apply-${INSTANCE}"
SUDOERS_FILE="/etc/sudoers.d/chat2api-worker-${INSTANCE}"
ENV_FILE="/etc/default/chat2api-worker-${INSTANCE}"

for port in "$PROXY_PORT" "$CDP_PORT"; do
  if ss -ltnH "sport = :${port}" 2>/dev/null | grep -q .; then
    echo "Slot ${SLOT} port ${port} is already in use" >&2
    exit 1
  fi
done
if [[ -e "/tmp/.X11-unix/X${DISPLAY_NUM}" ]] && ! systemctl is-active --quiet "$XVFB_UNIT"; then
  echo "X display :${DISPLAY_NUM} is already in use" >&2
  exit 1
fi

install -d -o root -g chat2api -m 750 "$CONFIG_DIR"
install -d -o root -g root -m 755 "$STATE_DIR"
install -d -o chat2api -g chat2api -m 700 "$PROFILE_DIR"

if [[ ! -s "$CONFIG_DIR/xray.json" ]]; then
  cat >"$CONFIG_DIR/xray.json" <<JSON
{"log":{"loglevel":"warning"},"inbounds":[{"listen":"127.0.0.1","port":${PROXY_PORT},"protocol":"socks","settings":{"udp":true}}],"outbounds":[{"protocol":"freedom","tag":"direct"}]}
JSON
fi
chown root:chat2api "$CONFIG_DIR/xray.json"
chmod 640 "$CONFIG_DIR/xray.json"

if [[ ! -s "$CONFIG_DIR/worker.json" ]]; then
  payload="$(jq -n --arg code "$ENROLL_CODE" --arg host "$(hostname)" --arg arch "$(uname -m)" --arg os "$(. /etc/os-release; printf '%s' "${PRETTY_NAME:-Linux}")" --arg slot "$INSTANCE" '{enroll_code:$code,hostname:$host,device_id:$host,platform:"linux",arch:$arch,os_version:$os,agent_version:"0.3.4",worker_slot:$slot}')"
  response="$(mktemp)"
  trap 'rm -f "${response:-}"' EXIT
  printf '%s' "$payload" | curl -fsSL --retry 3 --retry-all-errors -H 'Content-Type: application/json' --data-binary @- -o "$response" "$SERVER/api/workers/enroll"
  jq -e '.worker_id and .worker_token and .websocket_url' "$response" >/dev/null
  install -o root -g chat2api -m 640 "$response" "$CONFIG_DIR/worker.json"
  rm -f "$response"
  trap - EXIT
fi
jq -e 'type == "object" and (.worker_id|type=="string" and length>0) and (.worker_token|type=="string" and length>0) and (.websocket_url|type=="string" and length>0)' "$CONFIG_DIR/worker.json" >/dev/null

# Produce a root-owned helper whose paths/units are fixed to this slot. The
# unprivileged agent cannot redirect privileged writes through environment data.
python3 - "$WORKER_DIR/scripts/linux_worker_proxy_apply.sh" "$PROXY_HELPER" "$CONFIG_DIR" "$XRAY_UNIT" "$CHROME_UNIT" "$PROXY_PORT" <<'PY'
from pathlib import Path
import sys
src, dst, config_dir, xray_unit, chrome_unit, proxy_port = sys.argv[1:]
text = Path(src).read_text(encoding="utf-8")
replacements = {
    'XRAY_CONFIG="/etc/chat2api-worker/xray.json"': f'XRAY_CONFIG="{config_dir}/xray.json"',
    'XRAY_UNIT="chat2api-xray.service"': f'XRAY_UNIT="{xray_unit}"',
    'CHROME_UNIT="chat2api-chrome.service"': f'CHROME_UNIT="{chrome_unit}"',
    'PROXY_PORT="10808"': f'PROXY_PORT="{proxy_port}"',
    'WORKSPACE_PARENT="/etc/chat2api-worker"': f'WORKSPACE_PARENT="{config_dir}"',
}
for old, new in replacements.items():
    if old not in text:
        raise SystemExit(f"proxy helper template contract changed: {old}")
    text = text.replace(old, new, 1)
Path(dst).write_text(text, encoding="utf-8")
PY
chown root:root "$PROXY_HELPER"
chmod 755 "$PROXY_HELPER"

cat >"/etc/systemd/system/$XRAY_UNIT" <<UNIT
[Unit]
After=network-online.target
[Service]
User=chat2api
ExecStart=/usr/local/bin/xray run -c ${CONFIG_DIR}/xray.json
Restart=always
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
UNIT

cat >"/etc/systemd/system/$XVFB_UNIT" <<UNIT
[Service]
User=chat2api
ExecStart=/usr/bin/Xvfb :${DISPLAY_NUM} -screen 0 1920x1080x24 -nolisten tcp -ac
Restart=always
[Install]
WantedBy=multi-user.target
UNIT

cat >"/etc/systemd/system/$CHROME_UNIT" <<UNIT
[Unit]
Requires=${XRAY_UNIT} ${XVFB_UNIT}
After=${XRAY_UNIT} ${XVFB_UNIT}
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
Environment=CHAT2API_SERVER_URL=${SERVER}
ExecStart=/bin/bash ${WORKER_DIR}/scripts/linux_worker_slot_chrome_launcher.sh
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT

cat >"/etc/systemd/system/$AGENT_UNIT" <<UNIT
[Unit]
After=network-online.target ${CHROME_UNIT}
[Service]
User=chat2api
Environment=DISPLAY=:${DISPLAY_NUM}
Environment=CHAT2API_WORKER_SLOT=${SLOT}
Environment=CHAT2API_WORKER_CONFIG=${CONFIG_DIR}/worker.json
Environment=CHAT2API_XRAY_CONFIG=${CONFIG_DIR}/xray.json
Environment=CHAT2API_PROXY_APPLY_HELPER=${PROXY_HELPER}
Environment=CHAT2API_PROXY_PORT=${PROXY_PORT}
Environment=CHAT2API_LOGIN_DISPLAY=:${DISPLAY_NUM}
Environment=CHAT2API_LOGIN_CHROME_PROFILE=${PROFILE_DIR}
Environment=CHAT2API_LOGIN_CHROME_DEBUG_URL=http://127.0.0.1:${CDP_PORT}
ExecStart=${VENV_DIR}/bin/python ${WORKER_DIR}/scripts/linux_worker_slot_agent.py
Restart=always
RestartSec=5
ProtectSystem=strict
ReadWritePaths=${CONFIG_DIR}
[Install]
WantedBy=multi-user.target
UNIT

cat >"$ENV_FILE" <<ENV
REPO_DIR=${WORKER_DIR}
WORKER_USER=${WORKER_USER}
PROFILE_DIR=${PROFILE_DIR}
EXTENSION_DIR=${WORKER_DIR}/chrome_extension
PROXY_PORT=${PROXY_PORT}
CHATGPT_URL=https://chatgpt.com/
CHAT2API_SERVER_URL=${SERVER}
XRAY_UNIT=${XRAY_UNIT}
XVFB_UNIT=${XVFB_UNIT}
CHROME_UNIT=${CHROME_UNIT}
STATE_DIR=${STATE_DIR}
CHAT2API_EXTENSION_CENTRAL_SYNC=0
ENV
chmod 640 "$ENV_FILE"

cat >"/etc/systemd/system/${WATCHDOG_UNIT}.service" <<UNIT
[Service]
Type=oneshot
EnvironmentFile=${ENV_FILE}
ExecStart=${WORKER_DIR}/scripts/linux_worker_watchdog.sh
UNIT
cat >"/etc/systemd/system/${WATCHDOG_UNIT}.timer" <<UNIT
[Timer]
OnBootSec=90s
OnUnitActiveSec=2min
Persistent=true
[Install]
WantedBy=timers.target
UNIT
cat >"/etc/systemd/system/${AUTORELOAD_UNIT}.service" <<UNIT
[Service]
Type=oneshot
EnvironmentFile=${ENV_FILE}
ExecStart=${WORKER_DIR}/scripts/linux_extension_autoreload.sh
UNIT
cat >"/etc/systemd/system/${AUTORELOAD_UNIT}.timer" <<UNIT
[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
Persistent=true
[Install]
WantedBy=timers.target
UNIT

cat >"$SUDOERS_FILE" <<SUDO
chat2api ALL=(root) NOPASSWD: /bin/systemctl restart ${CHROME_UNIT}, /bin/systemctl restart ${XRAY_UNIT}, /bin/systemctl restart ${XVFB_UNIT}, ${PROXY_HELPER}
SUDO
chmod 440 "$SUDOERS_FILE"
visudo -cf "$SUDOERS_FILE" >/dev/null

systemctl daemon-reload
systemctl enable "$XRAY_UNIT" "$XVFB_UNIT" "$CHROME_UNIT" "$AGENT_UNIT" "${WATCHDOG_UNIT}.timer" "${AUTORELOAD_UNIT}.timer" >/dev/null
systemctl restart "$XRAY_UNIT"
systemctl restart "$XVFB_UNIT"
systemctl restart "$CHROME_UNIT"
systemctl restart "$AGENT_UNIT"
systemctl restart "${WATCHDOG_UNIT}.timer" "${AUTORELOAD_UNIT}.timer"

ready=0
for _ in $(seq 1 90); do
  if systemctl is-active --quiet "$XRAY_UNIT" \
    && systemctl is-active --quiet "$XVFB_UNIT" \
    && systemctl is-active --quiet "$CHROME_UNIT" \
    && systemctl is-active --quiet "$AGENT_UNIT" \
    && ps -u chat2api -o args= | grep -F -- "--user-data-dir=${PROFILE_DIR}" | grep -E '[Cc]hrome' >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
(( ready == 1 )) || { echo "Worker slot ${SLOT} did not become healthy" >&2; exit 1; }

echo "=== chat2api same-host Worker slot installed ==="
echo "Worker ID: $(jq -r .worker_id "$CONFIG_DIR/worker.json")"
echo "Host slot: ${SLOT}"
echo "Chrome profile: ${PROFILE_DIR}"
echo "SOCKS: 127.0.0.1:${PROXY_PORT}"
echo "Display: :${DISPLAY_NUM}"
echo "CDP: 127.0.0.1:${CDP_PORT}"
echo "Each slot has an independent Chrome/extension identity and ChatGPT login session."
