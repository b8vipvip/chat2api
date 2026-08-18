#!/usr/bin/env bash
set -euo pipefail

STAGE="arguments"
trap 'rc=$?; echo "[ERROR] stage=${STAGE} exit=${rc}" >&2; echo "Diagnose: journalctl -u chat2api-worker-agent -u chat2api-chrome -u chat2api-xray -n 100 --no-pager" >&2; exit ${rc}' ERR

SERVER="https://chat2api.mv3.cn"; ENROLL_CODE=""; REPO_URL="https://github.com/b8vipvip/chat2api.git"; REPO_DIR="/opt/chat2api"
while (($#)); do case "$1" in --server) SERVER="${2%/}"; shift 2;; --enroll-code) ENROLL_CODE="$2"; shift 2;; --repo-url) REPO_URL="$2"; shift 2;; *) echo "Unknown argument: $1" >&2; exit 2;; esac; done
[[ $EUID -eq 0 ]] || { echo "Run with sudo/root" >&2; exit 1; }
[[ -n "$ENROLL_CODE" ]] || { echo "--enroll-code is required" >&2; exit 2; }
[[ "$SERVER" == https://* || "${CHAT2API_ALLOW_INSECURE_HTTP:-0}" == 1 ]] || { echo "Server must use HTTPS" >&2; exit 2; }

STAGE="system-check"
. /etc/os-release
[[ "$ID" == ubuntu && ("$VERSION_ID" == 22.04 || "$VERSION_ID" == 24.04) ]] || { echo "Supported: Ubuntu 22.04/24.04" >&2; exit 1; }
[[ "$(uname -m)" == x86_64 ]] || { echo "Only x86_64 is currently supported" >&2; exit 1; }
[[ -d /run/systemd/system ]] || { echo "systemd is required" >&2; exit 1; }
(( $(df -Pk / | awk 'NR==2{print $4}') >= 2097152 )) || { echo "At least 2 GiB free disk is required" >&2; exit 1; }
getent hosts github.com >/dev/null; timedatectl show -p NTPSynchronized --value 2>/dev/null || true

STAGE="packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl wget git jq unzip xvfb xauth procps iproute2 lsof fonts-liberation python3 python3-venv sudo
if ! command -v google-chrome >/dev/null; then
  curl -fsSLo /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  apt-get install -y /tmp/google-chrome.deb; rm -f /tmp/google-chrome.deb
fi

STAGE="user-and-repository"
id chat2api >/dev/null 2>&1 || useradd --create-home --shell /usr/sbin/nologin chat2api
if [[ -d "$REPO_DIR/.git" ]]; then
  [[ -z "$(git -C "$REPO_DIR" status --porcelain)" ]] || { echo "$REPO_DIR has local changes; refusing overwrite" >&2; exit 1; }
  [[ "$(git -C "$REPO_DIR" remote get-url origin)" == "$REPO_URL" ]] || { echo "Unexpected repository origin" >&2; exit 1; }
  git -C "$REPO_DIR" pull --ff-only
else
  [[ ! -e "$REPO_DIR" ]] || { echo "$REPO_DIR exists but is not a Git repository" >&2; exit 1; }
  git clone --depth 1 "$REPO_URL" "$REPO_DIR"
fi
python3 -m venv /opt/chat2api-worker-venv
/opt/chat2api-worker-venv/bin/pip install --disable-pip-version-check 'websockets>=13,<16'

STAGE="xray"
if ! command -v xray >/dev/null; then
  XRAY_VERSION="$(curl -fsSL https://api.github.com/repos/XTLS/Xray-core/releases/latest | jq -er .tag_name)"
  curl -fsSLo /tmp/xray.zip "https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/Xray-linux-64.zip"
  curl -fsSLo /tmp/xray.dgst "https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/Xray-linux-64.zip.dgst"
  grep 'SHA2-256=' /tmp/xray.dgst | cut -d= -f2 | tr -d ' ' | awk '{print $1"  /tmp/xray.zip"}' | sha256sum -c -
  install -d -m 755 /usr/local/share/xray
  unzip -qo /tmp/xray.zip xray geoip.dat geosite.dat -d /usr/local/share/xray
  install -m 755 /usr/local/share/xray/xray /usr/local/bin/xray
fi
# Both Xray and the outbound Worker agent run as the unprivileged chat2api user.
# Keep the directory root-owned but group-traversable, while individual secret
# files remain readable only by root and the chat2api group.
install -d -o root -g chat2api -m 750 /etc/chat2api-worker
install -d -o root -g root -m 700 /var/lib/chat2api-worker/state
if [[ ! -s /etc/chat2api-worker/xray.json ]]; then
cat >/etc/chat2api-worker/xray.json <<'JSON'
{"log":{"loglevel":"warning"},"inbounds":[{"listen":"127.0.0.1","port":10808,"protocol":"socks","settings":{"udp":true}}],"outbounds":[{"protocol":"freedom","tag":"direct"}]}
JSON
fi
chown root:chat2api /etc/chat2api-worker/xray.json
chmod 640 /etc/chat2api-worker/xray.json

STAGE="enrollment"
if [[ ! -s /etc/chat2api-worker/worker.json ]]; then
  payload="$(jq -n --arg code "$ENROLL_CODE" --arg host "$(hostname)" --arg arch "$(uname -m)" --arg os "$PRETTY_NAME" '{enroll_code:$code,hostname:$host,device_id:$host,platform:"linux",arch:$arch,os_version:$os,agent_version:"0.1.0"}')"
  curl -fsSL -H 'Content-Type: application/json' -d "$payload" "$SERVER/api/workers/enroll" | jq -e '.worker_id and .worker_token and .websocket_url' >/etc/chat2api-worker/worker.json
fi
chown root:chat2api /etc/chat2api-worker/worker.json; chmod 640 /etc/chat2api-worker/worker.json
install -d -o chat2api -g chat2api -m 700 /home/chat2api/.config/chat2api-chrome-worker-01

STAGE="systemd"
cat >/etc/systemd/system/chat2api-xray.service <<'UNIT'
[Unit]
After=network-online.target
[Service]
User=chat2api
ExecStart=/usr/local/bin/xray run -c /etc/chat2api-worker/xray.json
Restart=always
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
UNIT
cat >/etc/systemd/system/chat2api-xvfb.service <<'UNIT'
[Service]
User=chat2api
ExecStart=/usr/bin/Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp -ac
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
cat >/etc/systemd/system/chat2api-chrome.service <<UNIT
[Unit]
Requires=chat2api-xray.service chat2api-xvfb.service
After=chat2api-xray.service chat2api-xvfb.service
[Service]
User=chat2api
Environment=HOME=/home/chat2api
Environment=DISPLAY=:99
ExecStart=/usr/bin/google-chrome --user-data-dir=/home/chat2api/.config/chat2api-chrome-worker-01 --password-store=basic --proxy-server=socks5://127.0.0.1:10808 --proxy-bypass-list=localhost\;127.0.0.1\;${SERVER#*://} --load-extension=${REPO_DIR}/chrome_extension --no-first-run --no-default-browser-check --disable-dev-shm-usage https://chatgpt.com/
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
cat >/etc/systemd/system/chat2api-worker-agent.service <<UNIT
[Unit]
After=network-online.target chat2api-chrome.service
[Service]
User=chat2api
ExecStart=/opt/chat2api-worker-venv/bin/python ${REPO_DIR}/scripts/linux_worker_agent.py
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ReadOnlyPaths=/etc/chat2api-worker
[Install]
WantedBy=multi-user.target
UNIT
install -m 755 "$REPO_DIR/scripts/linux_worker_watchdog.sh" /usr/local/sbin/chat2api-linux-worker-watchdog
install -m 755 "$REPO_DIR/scripts/linux_extension_autoreload.sh" /usr/local/sbin/chat2api-linux-extension-autoreload
cat >/etc/default/chat2api-worker-watchdog <<ENV
REPO_DIR=$REPO_DIR
WORKER_USER=chat2api
PROFILE_DIR=/home/chat2api/.config/chat2api-chrome-worker-01
EXTENSION_DIR=$REPO_DIR/chrome_extension
PROXY_PORT=10808
CHATGPT_URL=https://chatgpt.com/
CHAT2API_SERVER_URL=$SERVER
CHROME_UNIT=chat2api-chrome.service
STATE_DIR=/var/lib/chat2api-worker
ENV
cat >/etc/systemd/system/chat2api-worker-watchdog.service <<'UNIT'
[Service]
Type=oneshot
EnvironmentFile=/etc/default/chat2api-worker-watchdog
ExecStart=/usr/local/sbin/chat2api-linux-worker-watchdog
UNIT
cat >/etc/systemd/system/chat2api-worker-watchdog.timer <<'UNIT'
[Timer]
OnBootSec=90s
OnUnitActiveSec=2min
Persistent=true
[Install]
WantedBy=timers.target
UNIT
cat >/etc/systemd/system/chat2api-extension-autoreload.service <<'UNIT'
[Service]
Type=oneshot
EnvironmentFile=/etc/default/chat2api-worker-watchdog
ExecStart=/usr/local/sbin/chat2api-linux-extension-autoreload
UNIT
cat >/etc/systemd/system/chat2api-extension-autoreload.timer <<'UNIT'
[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
Persistent=true
[Install]
WantedBy=timers.target
UNIT
cat >/etc/sudoers.d/chat2api-worker <<'SUDO'
chat2api ALL=(root) NOPASSWD: /bin/systemctl restart chat2api-chrome.service, /bin/systemctl restart chat2api-xray.service, /bin/systemctl restart chat2api-xvfb.service
SUDO
chmod 440 /etc/sudoers.d/chat2api-worker; visudo -cf /etc/sudoers.d/chat2api-worker
systemctl daemon-reload
systemctl enable --now chat2api-xray chat2api-xvfb chat2api-chrome chat2api-worker-agent chat2api-worker-watchdog.timer chat2api-extension-autoreload.timer

echo "=== chat2api Linux Worker installed ==="
echo "Worker ID: $(jq -r .worker_id /etc/chat2api-worker/worker.json)"
echo "Server: $SERVER"; echo "Registration: enrolled"
for unit in xray xvfb chrome worker-agent worker-watchdog.timer extension-autoreload.timer; do echo "$unit: $(systemctl is-active chat2api-$unit || true)"; done
echo "Chrome Bridge: $(jq -r .version "$REPO_DIR/chrome_extension/manifest.json")"
echo "Next: $SERVER/admin#linux-workers"
