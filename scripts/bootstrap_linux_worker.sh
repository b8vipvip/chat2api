#!/usr/bin/env bash
set -euo pipefail

STAGE="arguments"
SERVER="https://chat2api.mv3.cn"
ENROLL_CODE=""
WORKER_DIR="/opt/chat2api-worker"
PROFILE_DIR="/home/chat2api/.config/chat2api-chrome-worker-01"
PIP_INDEX_URL="${CHAT2API_PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
INSTALL_SUCCESS=0
LAST_MESSAGE="安装过程异常退出"

while (($#)); do
  case "$1" in
    --server) SERVER="${2%/}"; shift 2;;
    --enroll-code) ENROLL_CODE="$2"; shift 2;;
    --repo-url) echo "[INFO] --repo-url 已弃用；Worker 代码现在从中心服务器 Bundle 获取" >&2; shift 2;;
    *) echo "Unknown argument: $1" >&2; exit 2;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "Run with sudo/root" >&2; exit 1; }
[[ -n "$ENROLL_CODE" ]] || { echo "--enroll-code is required" >&2; exit 2; }
[[ "$SERVER" == https://* || "${CHAT2API_ALLOW_INSECURE_HTTP:-0}" == 1 ]] || { echo "Server must use HTTPS" >&2; exit 2; }

report_progress() {
  local state="$1" stage="$2" message="$3"
  command -v python3 >/dev/null 2>&1 || return 0
  {
    printf '%s\n' "$ENROLL_CODE" "$state" "$stage" "$message" "$(hostname 2>/dev/null || true)" "$(uname -m 2>/dev/null || true)"
    if [[ -r /etc/os-release ]]; then . /etc/os-release; printf '%s\n' "${PRETTY_NAME:-}"; else printf '\n'; fi
  } | python3 -c 'import json,sys; v=[x.rstrip("\n") for x in sys.stdin.readlines()]; print(json.dumps({"enroll_code":v[0],"state":v[1],"stage":v[2],"message":v[3],"hostname":v[4],"arch":v[5],"os_version":v[6]}))' \
    | curl -fsS --connect-timeout 8 --max-time 15 -H 'Content-Type: application/json' --data-binary @- "$SERVER/api/workers/install-progress" >/dev/null 2>&1 || true
}

set_stage() {
  STAGE="$1"; LAST_MESSAGE="$2"
  echo "[${STAGE}] ${LAST_MESSAGE}"
  report_progress "installing" "$STAGE" "$LAST_MESSAGE"
}

on_exit() {
  local rc=$?
  if [[ $rc -ne 0 && $INSTALL_SUCCESS -ne 1 ]]; then
    report_progress "failed" "$STAGE" "${LAST_MESSAGE}（exit=${rc}）"
    echo "[ERROR] stage=${STAGE} exit=${rc}" >&2
    echo "Diagnose: journalctl -u chat2api-worker-agent -u chat2api-chrome -u chat2api-xray -n 100 --no-pager" >&2
  fi
}
trap on_exit EXIT

set_stage "system-check" "检查 Ubuntu、架构、systemd、磁盘和中心服务器"
. /etc/os-release
[[ "$ID" == ubuntu && ("$VERSION_ID" == 22.04 || "$VERSION_ID" == 24.04) ]] || { LAST_MESSAGE="仅支持 Ubuntu 22.04/24.04"; exit 1; }
[[ "$(uname -m)" == x86_64 ]] || { LAST_MESSAGE="当前仅支持 x86_64"; exit 1; }
[[ -d /run/systemd/system ]] || { LAST_MESSAGE="systemd 不可用"; exit 1; }
(( $(df -Pk / | awk 'NR==2{print $4}') >= 2097152 )) || { LAST_MESSAGE="根分区至少需要 2 GiB 可用空间"; exit 1; }
curl -fsS --retry 3 --retry-all-errors --connect-timeout 8 --max-time 20 "$SERVER/version" >/dev/null
id chat2api >/dev/null 2>&1 || useradd --create-home --shell /usr/sbin/nologin chat2api
install -d -o chat2api -g chat2api -m 700 "$PROFILE_DIR"

set_stage "cleanup" "检查并清理上次未完成安装残留（保留 Chrome Profile）"
VALID_IDENTITY=0
if [[ -s /etc/chat2api-worker/worker.json ]] && python3 - /etc/chat2api-worker/worker.json <<'PY' >/dev/null 2>&1
import json,sys
obj=json.load(open(sys.argv[1],encoding='utf-8'))
assert obj.get('worker_id') and obj.get('worker_token') and obj.get('websocket_url')
PY
then
  VALID_IDENTITY=1
fi
if [[ $VALID_IDENTITY -eq 0 ]]; then
  for unit in \
    chat2api-worker-agent.service chat2api-chrome.service chat2api-xray.service chat2api-xvfb.service \
    chat2api-worker-watchdog.timer chat2api-worker-watchdog.service \
    chat2api-extension-autoreload.timer chat2api-extension-autoreload.service; do
    systemctl disable --now "$unit" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/$unit"
  done
  rm -f /etc/default/chat2api-worker-watchdog /etc/sudoers.d/chat2api-worker
  rm -f /usr/local/sbin/chat2api-linux-worker-watchdog /usr/local/sbin/chat2api-linux-extension-autoreload /usr/local/sbin/chat2api-worker-proxy-apply
  rm -rf /opt/chat2api-worker-venv "$WORKER_DIR" /etc/chat2api-worker /var/lib/chat2api-worker
  systemctl daemon-reload
  systemctl reset-failed >/dev/null 2>&1 || true
else
  echo "[cleanup] 检测到完整 Worker 身份，按幂等升级处理，不删除身份和 Profile"
fi

set_stage "packages" "安装 Worker 基础依赖（沿用系统现有 APT 镜像）"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl wget git jq unzip xvfb xauth x11-apps xdotool imagemagick procps iproute2 lsof fonts-liberation python3 python3-venv sudo
if ! command -v google-chrome >/dev/null; then
  LAST_MESSAGE="下载并安装 Google Chrome"
  report_progress "installing" "$STAGE" "$LAST_MESSAGE"
  curl -fSL --retry 5 --retry-delay 2 --retry-all-errors --connect-timeout 15 --max-time 300 -o /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  apt-get install -y /tmp/google-chrome.deb
  rm -f /tmp/google-chrome.deb
fi

set_stage "worker-bundle" "从 chat2api 中心服务器下载并校验 Worker Bundle"
BUNDLE_META="$(mktemp)"; BUNDLE_FILE="$(mktemp)"; BUNDLE_TMP="$(mktemp -d)"
curl -fSL --retry 5 --retry-delay 2 --retry-all-errors --connect-timeout 10 --max-time 120 -o "$BUNDLE_META" "$SERVER/bootstrap/linux-worker-bundle.json"
EXPECTED_SHA="$(jq -er '.sha256' "$BUNDLE_META")"
curl -fSL --retry 5 --retry-delay 2 --retry-all-errors --connect-timeout 10 --max-time 300 -o "$BUNDLE_FILE" "$SERVER/bootstrap/linux-worker-bundle.tar.gz"
echo "$EXPECTED_SHA  $BUNDLE_FILE" | sha256sum -c -
tar -xzf "$BUNDLE_FILE" -C "$BUNDLE_TMP"
[[ -f "$BUNDLE_TMP/chrome_extension/manifest.json" && -f "$BUNDLE_TMP/scripts/linux_worker_agent.py" ]] || { LAST_MESSAGE="Worker Bundle 内容不完整"; exit 1; }
rm -rf "${WORKER_DIR}.new"
install -d -m 755 "${WORKER_DIR}.new"
cp -a "$BUNDLE_TMP"/. "${WORKER_DIR}.new"/
if [[ -d "$WORKER_DIR" ]]; then rm -rf "${WORKER_DIR}.previous"; mv "$WORKER_DIR" "${WORKER_DIR}.previous"; fi
mv "${WORKER_DIR}.new" "$WORKER_DIR"
rm -rf "${WORKER_DIR}.previous" "$BUNDLE_TMP"
rm -f "$BUNDLE_META" "$BUNDLE_FILE"

set_stage "python" "创建 Worker Python 环境并从国内 PyPI 镜像安装运行库"
rm -rf /opt/chat2api-worker-venv
python3 -m venv /opt/chat2api-worker-venv
PIP_DEFAULT_TIMEOUT=120 /opt/chat2api-worker-venv/bin/pip install --disable-pip-version-check --retries 5 --timeout 120 --index-url "$PIP_INDEX_URL" 'websockets>=13,<16'

set_stage "xray" "从 chat2api 中心服务器下载并校验 Xray Core"
if ! command -v xray >/dev/null; then
  XRAY_META="$(mktemp)"; XRAY_ZIP="$(mktemp)"
  curl -fSL --retry 5 --retry-delay 2 --retry-all-errors --connect-timeout 10 --max-time 180 -o "$XRAY_META" "$SERVER/bootstrap/xray/latest.json"
  XRAY_SHA="$(jq -er '.sha256' "$XRAY_META")"
  curl -fSL --retry 5 --retry-delay 2 --retry-all-errors --connect-timeout 10 --max-time 600 -o "$XRAY_ZIP" "$SERVER/bootstrap/xray/latest.zip"
  echo "$XRAY_SHA  $XRAY_ZIP" | sha256sum -c -
  install -d -m 755 /usr/local/share/xray
  unzip -qo "$XRAY_ZIP" xray geoip.dat geosite.dat -d /usr/local/share/xray
  install -m 755 /usr/local/share/xray/xray /usr/local/bin/xray
  rm -f "$XRAY_META" "$XRAY_ZIP"
fi
install -d -o root -g chat2api -m 750 /etc/chat2api-worker
install -d -o root -g root -m 700 /var/lib/chat2api-worker/state
if [[ ! -s /etc/chat2api-worker/xray.json ]]; then
cat >/etc/chat2api-worker/xray.json <<'JSON'
{"log":{"loglevel":"warning"},"inbounds":[{"listen":"127.0.0.1","port":10808,"protocol":"socks","settings":{"udp":true}}],"outbounds":[{"protocol":"freedom","tag":"direct"}]}
JSON
fi
chown root:chat2api /etc/chat2api-worker/xray.json
chmod 640 /etc/chat2api-worker/xray.json

set_stage "enrollment" "向中心服务器注册 Worker 身份"
report_progress "enrolling" "$STAGE" "$LAST_MESSAGE"
if [[ ! -s /etc/chat2api-worker/worker.json ]]; then
  payload="$(jq -n --arg code "$ENROLL_CODE" --arg host "$(hostname)" --arg arch "$(uname -m)" --arg os "$PRETTY_NAME" '{enroll_code:$code,hostname:$host,device_id:$host,platform:"linux",arch:$arch,os_version:$os,agent_version:"0.3.1"}')"
  printf '%s' "$payload" | curl -fsSL --retry 3 --retry-all-errors -H 'Content-Type: application/json' --data-binary @- "$SERVER/api/workers/enroll" | jq -e '.worker_id and .worker_token and .websocket_url' >/etc/chat2api-worker/worker.json
fi
chown root:chat2api /etc/chat2api-worker/worker.json
chmod 640 /etc/chat2api-worker/worker.json
install -d -o chat2api -g chat2api -m 700 "$PROFILE_DIR"

set_stage "systemd" "安装并启动 Xray、Xvfb、Chrome、Agent、Watchdog"
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
ExecStart=/usr/bin/google-chrome --user-data-dir=${PROFILE_DIR} --password-store=basic --proxy-server=socks5://127.0.0.1:10808 --proxy-bypass-list=localhost\;127.0.0.1\;${SERVER#*://} --load-extension=${WORKER_DIR}/chrome_extension --no-first-run --no-default-browser-check --disable-dev-shm-usage https://chatgpt.com/
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
cat >/etc/systemd/system/chat2api-worker-agent.service <<UNIT
[Unit]
After=network-online.target chat2api-chrome.service
[Service]
User=chat2api
Environment=DISPLAY=:99
ExecStart=/opt/chat2api-worker-venv/bin/python ${WORKER_DIR}/scripts/linux_worker_agent.py
Restart=always
RestartSec=5
ProtectSystem=strict
ReadWritePaths=/etc/chat2api-worker
[Install]
WantedBy=multi-user.target
UNIT
install -m 755 "$WORKER_DIR/scripts/linux_worker_watchdog.sh" /usr/local/sbin/chat2api-linux-worker-watchdog
install -m 755 "$WORKER_DIR/scripts/linux_extension_autoreload.sh" /usr/local/sbin/chat2api-linux-extension-autoreload
install -o root -g root -m 755 "$WORKER_DIR/scripts/linux_worker_proxy_apply.sh" /usr/local/sbin/chat2api-worker-proxy-apply
cat >/etc/default/chat2api-worker-watchdog <<ENV
REPO_DIR=$WORKER_DIR
WORKER_USER=chat2api
PROFILE_DIR=$PROFILE_DIR
EXTENSION_DIR=$WORKER_DIR/chrome_extension
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
chat2api ALL=(root) NOPASSWD: /bin/systemctl restart chat2api-chrome.service, /bin/systemctl restart chat2api-xray.service, /bin/systemctl restart chat2api-xvfb.service, /usr/local/sbin/chat2api-worker-proxy-apply
SUDO
chmod 440 /etc/sudoers.d/chat2api-worker
visudo -cf /etc/sudoers.d/chat2api-worker
systemctl daemon-reload
systemctl enable --now chat2api-xray chat2api-xvfb chat2api-chrome chat2api-worker-agent chat2api-worker-watchdog.timer chat2api-extension-autoreload.timer

set_stage "health" "验证 Worker 服务状态"
for unit in chat2api-xray.service chat2api-xvfb.service chat2api-chrome.service chat2api-worker-agent.service; do
  systemctl is-active --quiet "$unit" || { LAST_MESSAGE="$unit 未正常运行"; exit 1; }
done

INSTALL_SUCCESS=1
report_progress "installed" "complete" "Worker 安装完成并已启动"
echo "=== chat2api Linux Worker installed ==="
echo "Worker ID: $(jq -r .worker_id /etc/chat2api-worker/worker.json)"
echo "Server: $SERVER"
echo "Registration: enrolled"
for unit in xray xvfb chrome worker-agent worker-watchdog.timer extension-autoreload.timer; do echo "$unit: $(systemctl is-active chat2api-$unit || true)"; done
echo "Chrome Bridge: $(jq -r .version "$WORKER_DIR/chrome_extension/manifest.json")"
echo "Next: $SERVER/admin#linux-workers"