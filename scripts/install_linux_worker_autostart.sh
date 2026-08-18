#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd -- "${SCRIPT_DIR}/.." && pwd)}"
WORKER_USER="${WORKER_USER:-chat2api}"
PROFILE_DIR="${PROFILE_DIR:-/home/${WORKER_USER}/.config/chat2api-chrome-worker-01}"
WORKER_CONFIG_DIR="${WORKER_CONFIG_DIR:-/home/${WORKER_USER}/.config/chat2api-worker}"
EXTENSION_DIR="${EXTENSION_DIR:-${REPO_DIR}/chrome_extension}"
PROXY_PORT="${PROXY_PORT:-10808}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"
CHATGPT_URL="${CHATGPT_URL:-https://chatgpt.com/}"
CHAT2API_SERVER_URL="${CHAT2API_SERVER_URL:-https://chat2api.mv3.cn}"
BYPASS_LIST="${BYPASS_LIST:-localhost;127.0.0.1;chat2api.mv3.cn}"
WATCHDOG_SOURCE="${SCRIPT_DIR}/linux_worker_watchdog.sh"
WATCHDOG_BIN="/usr/local/sbin/chat2api-linux-worker-watchdog"
WATCHDOG_ENV="/etc/default/chat2api-worker-watchdog"
AUTORELOAD_SOURCE="${SCRIPT_DIR}/linux_extension_autoreload.sh"
AUTORELOAD_BIN="/usr/local/sbin/chat2api-linux-extension-autoreload"
AUTORELOAD_STATE_DIR="${AUTORELOAD_STATE_DIR:-/var/lib/chat2api-worker}"

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

if [[ ! -f "${EXTENSION_DIR}/manifest.json" ]]; then
  echo "Chrome Bridge source not found: ${EXTENSION_DIR}/manifest.json" >&2
  exit 1
fi

if [[ ! -f "${WATCHDOG_SOURCE}" ]]; then
  echo "Linux worker watchdog source not found: ${WATCHDOG_SOURCE}" >&2
  exit 1
fi

if [[ ! -f "${AUTORELOAD_SOURCE}" ]]; then
  echo "Linux extension auto-reload source not found: ${AUTORELOAD_SOURCE}" >&2
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
xray_dir="$(dirname "${xray_bin}")"
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

find_geodata() {
  local name="$1"
  local candidate
  for candidate in \
    "${xray_dir}/${name}" \
    "${xray_cwd}/${name}" \
    "$(dirname "${xray_dir}")/${name}" \
    "/home/${WORKER_USER}/.local/share/v2rayN/bin/${name}" \
    "/home/${WORKER_USER}/.local/share/v2rayN/bin/xray/${name}"; do
    if [[ -f "${candidate}" ]]; then
      readlink -f "${candidate}"
      return 0
    fi
  done
  return 1
}

source_geosite="$(find_geodata geosite.dat || true)"
source_geoip="$(find_geodata geoip.dat || true)"
if [[ -z "${source_geosite}" ]]; then
  echo "Could not locate geosite.dat required by the active Xray routing config." >&2
  echo "Expected it near ${xray_bin} or the v2rayN bin directory. The live proxy has not been stopped." >&2
  exit 1
fi

# Xray resolves geosite.dat/geoip.dat relative to its executable directory in
# this v2rayN layout. Copy the assets before stopping the known-good live proxy,
# so a missing geodata file cannot leave the worker without a proxy listener.
if [[ "${source_geosite}" != "${xray_dir}/geosite.dat" ]]; then
  install -o "${WORKER_USER}" -g "${WORKER_USER}" -m 644 "${source_geosite}" "${xray_dir}/geosite.dat"
fi
if [[ -n "${source_geoip}" && "${source_geoip}" != "${xray_dir}/geoip.dat" ]]; then
  install -o "${WORKER_USER}" -g "${WORKER_USER}" -m 644 "${source_geoip}" "${xray_dir}/geoip.dat"
fi

install -d -o "${WORKER_USER}" -g "${WORKER_USER}" -m 700 "${WORKER_CONFIG_DIR}"
captured_config="${WORKER_CONFIG_DIR}/xray-config.json"
source_config_real="$(readlink -f "${source_config}")"
captured_config_real="$(readlink -m "${captured_config}")"
if [[ "${source_config_real}" == "${captured_config_real}" ]]; then
  # Re-running the installer after systemd has already taken over is a normal
  # production update path. Do not call install(1) with the same source and
  # destination, because GNU install rejects that and would prevent new units
  # (watchdog/extension auto-reload) from being installed.
  chown "${WORKER_USER}:${WORKER_USER}" "${captured_config}"
  chmod 600 "${captured_config}"
else
  install -o "${WORKER_USER}" -g "${WORKER_USER}" -m 600 "${source_config}" "${captured_config}"
fi
install -d -o "${WORKER_USER}" -g "${WORKER_USER}" -m 700 "${PROFILE_DIR}"
install -o root -g root -m 755 "${WATCHDOG_SOURCE}" "${WATCHDOG_BIN}"
install -o root -g root -m 755 "${AUTORELOAD_SOURCE}" "${AUTORELOAD_BIN}"
install -d -o root -g root -m 755 "${AUTORELOAD_STATE_DIR}"

cat >"${WATCHDOG_ENV}" <<EOF
REPO_DIR=${REPO_DIR}
WORKER_USER=${WORKER_USER}
PROFILE_DIR=${PROFILE_DIR}
EXTENSION_DIR=${EXTENSION_DIR}
PROXY_PORT=${PROXY_PORT}
CHATGPT_URL=${CHATGPT_URL}
CHAT2API_SERVER_URL=${CHAT2API_SERVER_URL}
CHROME_UNIT=chat2api-chrome.service
STATE_DIR=${AUTORELOAD_STATE_DIR}
EOF
chmod 644 "${WATCHDOG_ENV}"

# Validate the captured configuration while the original live proxy is still
# running. Xray's test mode does not bind the inbound port, so failure here is a
# safe preflight and leaves the current working proxy untouched.
if ! "${xray_bin}" run -test -c "${captured_config}" >/tmp/chat2api-xray-preflight.log 2>&1; then
  echo "Captured Xray config failed preflight. The live proxy has not been stopped." >&2
  cat /tmp/chat2api-xray-preflight.log >&2 || true
  exit 1
fi
rm -f /tmp/chat2api-xray-preflight.log

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
ExecStart=${xray_bin} run -c ${captured_config}
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

cat >/etc/systemd/system/chat2api-worker-watchdog.service <<EOF
[Unit]
Description=chat2api Linux worker health check and conservative self-heal
Wants=network-online.target
After=network-online.target chat2api-xray.service chat2api-xvfb.service chat2api-chrome.service

[Service]
Type=oneshot
EnvironmentFile=-${WATCHDOG_ENV}
ExecStart=${WATCHDOG_BIN}
TimeoutStartSec=60
EOF

cat >/etc/systemd/system/chat2api-worker-watchdog.timer <<'EOF'
[Unit]
Description=Periodically verify the chat2api Linux worker

[Timer]
OnBootSec=90s
OnUnitActiveSec=2min
AccuracySec=15s
RandomizedDelaySec=10s
Persistent=true
Unit=chat2api-worker-watchdog.service

[Install]
WantedBy=timers.target
EOF

cat >/etc/systemd/system/chat2api-extension-autoreload.service <<EOF
[Unit]
Description=Apply updated chat2api unpacked Chrome Bridge source
After=chat2api-chrome.service
# Autoreload intentionally restarts Chrome. A hard Requires= dependency would
# cause systemd to SIGTERM this oneshot when Chrome is stopped for that restart.
Wants=chat2api-chrome.service

[Service]
Type=oneshot
EnvironmentFile=-${WATCHDOG_ENV}
ExecStart=${AUTORELOAD_BIN}
TimeoutStartSec=60
EOF

cat >/etc/systemd/system/chat2api-extension-autoreload.timer <<'EOF'
[Unit]
Description=Detect updated chat2api Chrome Bridge source

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=10s
RandomizedDelaySec=5s
Persistent=true
Unit=chat2api-extension-autoreload.service

[Install]
WantedBy=timers.target
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
systemctl enable \
  chat2api-xray.service \
  chat2api-xvfb.service \
  chat2api-chrome.service \
  chat2api-worker-watchdog.timer \
  chat2api-extension-autoreload.timer
systemctl restart chat2api-xray.service
systemctl restart chat2api-xvfb.service
systemctl restart chat2api-chrome.service
systemctl restart chat2api-worker-watchdog.timer
# Chrome has just been restarted from the current repository contents, so the
# first auto-reload run records that source tree as the deployed baseline.
systemctl start chat2api-extension-autoreload.service
systemctl restart chat2api-extension-autoreload.timer

sleep 3

echo "=== chat2api Linux worker autostart installed ==="
echo "Xray binary: ${xray_bin}"
echo "Captured config: ${source_config} -> ${captured_config}"
echo "geosite.dat: ${source_geosite} -> ${xray_dir}/geosite.dat"
if [[ -n "${source_geoip}" ]]; then
  echo "geoip.dat: ${source_geoip} -> ${xray_dir}/geoip.dat"
fi
echo "Chrome profile: ${PROFILE_DIR}"
echo "Chrome Bridge source: ${EXTENSION_DIR}"
echo "Proxy: socks5://127.0.0.1:${PROXY_PORT}"
echo "Virtual display: :${DISPLAY_NUM}"
echo "Watchdog: ${WATCHDOG_BIN} (systemd timer every ~2 minutes)"
echo "Extension auto-reload: ${AUTORELOAD_BIN} (checks source every ~1 minute)"
echo
echo "Service status:"
for unit in \
  chat2api-xray \
  chat2api-xvfb \
  chat2api-chrome \
  chat2api-worker-watchdog.timer \
  chat2api-extension-autoreload.timer; do
  printf '%-40s ' "${unit}"
  systemctl is-active "${unit}" || true
done

echo
echo "Proxy listener:"
ss -lntp | grep ":${PROXY_PORT}" || true

echo
echo "Chrome worker process:"
pgrep -af "google-chrome.*chat2api-chrome-worker-01" | head -5 || true

echo
echo "Extension deployment state:"
cat "${AUTORELOAD_STATE_DIR}/extension-state.env" 2>/dev/null || true

echo
echo "Useful commands:"
echo "  systemctl status chat2api-xray chat2api-xvfb chat2api-chrome chat2api-worker-watchdog.timer chat2api-extension-autoreload.timer --no-pager"
echo "  systemctl start chat2api-worker-watchdog.service"
echo "  systemctl start chat2api-extension-autoreload.service"
echo "  journalctl -u chat2api-worker-watchdog -n 100 --no-pager"
echo "  journalctl -u chat2api-extension-autoreload -n 100 --no-pager"
echo "  systemctl restart chat2api-chrome"