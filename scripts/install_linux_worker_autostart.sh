#!/usr/bin/env bash
set -euo pipefail

WORKER_USER="${WORKER_USER:-chat2api}"
PROFILE_DIR="${PROFILE_DIR:-/home/${WORKER_USER}/.config/chat2api-chrome-worker-01}"
WORKER_CONFIG_DIR="${WORKER_CONFIG_DIR:-/home/${WORKER_USER}/.config/chat2api-worker}"
PROXY_PORT="${PROXY_PORT:-10808}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"
CHATGPT_URL="${CHATGPT_URL:-https://chatgpt.com/}"
BYPASS_LIST="${BYPASS_LIST:-localhost;127.0.0.1;chat2api.mv3.cn}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

if ! id "${WORKER_USER}" >/dev/null 2>&1; then
  echo "Worker user not found: ${WORKER_USER}" >&2
  exit 1
fi

if [[ ! -x /usr/bin/google-chrome ]]; then
  echo "Google Chrome not found at /usr/bin/google-chrome" >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y xvfb curl

proxy_pid="$(ss -lntp "sport = :${PROXY_PORT}" 2>/dev/null | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | head -n1 || true)"
if [[ -z "${proxy_pid}" || ! -e "/proc/${proxy_pid}/exe" ]]; then
  echo "No running proxy core was found on 127.0.0.1:${PROXY_PORT}. Start the working v2rayN/Xray node first." >&2
  exit 1
fi

xray_bin="$(readlink -f "/proc/${proxy_pid}/exe")"
xray_cwd="$(readlink -f "/proc/${proxy_pid}/cwd")"
proxy_user="$(stat -c '%U' "/proc/${proxy_pid}")"
if [[ "${proxy_user}" != "${WORKER_USER}" ]]; then
  echo "Proxy on port ${PROXY_PORT} belongs to ${proxy_user}, expected ${WORKER_USER}." >&2
  exit 1
fi
if [[ "$(basename "${xray_bin}")" != "xray" ]]; then
  echo "Process on port ${PROXY_PORT} is not Xray: ${xray_bin}" >&2
  exit 1
fi

cmdline="$(tr '\0' ' ' < "/proc/${proxy_pid}/cmdline")"
source_config=""
read -r -a argv <<<"${cmdline}"
for ((i=0; i<${#argv[@]}; i++)); do
  if [[ "${argv[$i]}" == "-c" || "${argv[$i]}" == "-config" ]]; then
    if (( i + 1 < ${#argv[@]} )); then
      source_config="${argv[$((i+1))]}"
      break
    fi
  fi
done
if [[ -z "${source_config}" ]]; then
  source_config="config.json"
fi
if [[ "${source_config}" != /* ]]; then
  source_config="${xray_cwd}/${source_config}"
fi
if [[ ! -f "${source_config}" ]]; then
  echo "Could not locate the live Xray config: ${source_config}" >&2
  exit 1
fi

install -d -o "${WORKER_USER}" -g "${WORKER_USER}" -m 700 "${WORKER_CONFIG_DIR}"
install -o "${WORKER_USER}" -g "${WORKER_USER}" -m 600 "${source_config}" "${WORKER_CONFIG_DIR}/xray-config.json"
install -d -o "${WORKER_USER}" -g "${WORKER_USER}" -m 700 "${PROFILE_DIR}"

cat >/etc/systemd/system/chat2api-xray.service <<EOF
[Unit]
Description=chat2api Linux worker Xray proxy
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${WORKER_USER}
Group=${WORKER_USER}
WorkingDirectory=${xray_cwd}
ExecStart=${xray_bin} run -c ${WORKER_CONFIG_DIR}/xray-config.json
Restart=always
RestartSec=3
NoNewPrivileges=true
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/chat2api-xvfb.service <<EOF
[Unit]
Description=chat2api Linux worker virtual X display
After=network.target

[Service]
Type=simple
User=${WORKER_USER}
Group=${WORKER_USER}
ExecStart=/usr/bin/Xvfb :${DISPLAY_NUM} -screen 0 1920x1080x24 -nolisten tcp -ac
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/chat2api-chrome.service <<EOF
[Unit]
Description=chat2api Linux Chrome Bridge worker
Wants=network-online.target
After=network-online.target chat2api-xray.service chat2api-xvfb.service
Requires=chat2api-xray.service chat2api-xvfb.service

[Service]
Type=simple
User=${WORKER_USER}
Group=${WORKER_USER}
Environment=HOME=/home/${WORKER_USER}
Environment=DISPLAY=:${DISPLAY_NUM}
RuntimeDirectory=chat2api-chrome
RuntimeDirectoryMode=0700
Environment=XDG_RUNTIME_DIR=/run/chat2api-chrome
ExecStartPre=/bin/bash -c 'for i in {1..30}; do (echo >/dev/tcp/127.0.0.1/${PROXY_PORT}) >/dev/null 2>&1 && exit 0; sleep 2; done; exit 1'
ExecStart=/usr/bin/google-chrome --user-data-dir=${PROFILE_DIR} --password-store=basic --proxy-server=socks5://127.0.0.1:${PROXY_PORT} --proxy-bypass-list=${BYPASS_LIST} --ozone-platform=x11 --no-first-run --no-default-browser-check --disable-dev-shm-usage --disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding --window-size=1280,900 ${CHATGPT_URL}
Restart=always
RestartSec=5
KillMode=control-group
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF

# Stop only the dedicated worker Chrome profile. Other Chrome processes on the
# host (for example BT-Panel helpers) are intentionally left alone.
pkill -TERM -u "${WORKER_USER}" -f "user-data-dir=${PROFILE_DIR}" 2>/dev/null || true
sleep 2

# The v2rayN GUI may already be closed while its Xray child is still alive. Stop
# only the core currently owning the configured proxy port before systemd takes it over.
kill "${proxy_pid}" 2>/dev/null || true
for _ in {1..20}; do
  if ! kill -0 "${proxy_pid}" 2>/dev/null; then break; fi
  sleep 0.25
done

systemctl daemon-reload
systemctl enable chat2api-xray.service chat2api-xvfb.service chat2api-chrome.service
systemctl restart chat2api-xray.service
systemctl restart chat2api-xvfb.service
systemctl restart chat2api-chrome.service

sleep 3

echo "=== chat2api Linux worker autostart installed ==="
echo "Xray binary: ${xray_bin}"
echo "Captured config: ${source_config} -> ${WORKER_CONFIG_DIR}/xray-config.json"
echo "Chrome profile: ${PROFILE_DIR}"
echo "Proxy: socks5://127.0.0.1:${PROXY_PORT}"
echo "Virtual display: :${DISPLAY_NUM}"
echo
echo "Service status:"
for unit in chat2api-xray chat2api-xvfb chat2api-chrome; do
  printf '%-20s ' "${unit}"
  systemctl is-active "${unit}.service" || true
done

echo
echo "Proxy listener:"
ss -lntp | grep ":${PROXY_PORT}" || true

echo
echo "Chrome worker process:"
pgrep -af "google-chrome.*chat2api-chrome-worker-01" | head -5 || true

echo
echo "Useful commands:"
echo "  systemctl status chat2api-xray chat2api-xvfb chat2api-chrome --no-pager"
echo "  journalctl -u chat2api-chrome -n 100 --no-pager"
echo "  systemctl restart chat2api-chrome"
